"""Model transport: OpenAI-compatible endpoints or the Claude SDK; explicit timeouts.

Embeddings always use the OpenAI-compatible embedding endpoint (for example Ollama).
"""
import copy
import httpx
import json
import time
import base64
import re
from threading import Lock
from afs_assistant.config import Settings
from afs_assistant.core.embeddings import validate_vectors

# Claude structured outputs reject these JSON-schema keywords; callers still validate them with Pydantic.
CLAUDE_UNSUPPORTED_SCHEMA_KEYS = {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
                                  "exclusiveMaximum", "multipleOf", "minItems", "maxItems", "pattern", "default", "title"}
CLAUDE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
# Thinking is always on for current Claude models and counts toward max_tokens.
# Evidence-rich answers need room; the SDK requires streaming above ~21,333 tokens.
CLAUDE_MIN_OUTPUT_TOKENS = 20000


class VisionTokenLimitError(ValueError):
    """The provider exhausted its output budget without a complete description."""


class StructuredResponseError(ValueError):
    """The model returned incomplete or unusable structured output."""


class TruncatedAnswerError(StructuredResponseError):
    """The structured answer was cut off at the output token limit."""


def claude_schema(node):
    """Copy a JSON schema without keywords Claude's structured outputs do not accept.

    Walks schema positions only, so a field *named* "title" or "pattern" is kept.
    """
    if not isinstance(node, dict):
        return copy.deepcopy(node)
    result = {}
    for key, value in node.items():
        if key in CLAUDE_UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
            result[key] = {name: claude_schema(item) for name, item in value.items()}
        elif key in {"items", "not"} and isinstance(value, dict):
            result[key] = claude_schema(value)
        elif key in {"anyOf", "allOf", "oneOf", "prefixItems"} and isinstance(value, list):
            result[key] = [claude_schema(item) for item in value]
        else:
            result[key] = copy.deepcopy(value)
    if result.get("type") == "object":
        result["additionalProperties"] = False
    return result


def claude_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Split OpenAI-style messages into Claude's system prompt and alternating turns."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns: list[dict] = []
    for message in messages:
        if message["role"] == "system":
            continue
        if turns and turns[-1]["role"] == message["role"] and isinstance(turns[-1]["content"], str) and isinstance(message["content"], str):
            turns[-1]["content"] += "\n\n" + message["content"]
        else:
            turns.append({"role": message["role"], "content": message["content"]})
    return system, turns


class Gateway:
    VISION_TRANSPORT = "jpeg-1920-max600k-v1"

    @staticmethod
    def vision_image_url(pixels: bytes) -> str:
        """Bound request size; keep retained document pixels untouched."""
        if len(pixels) <= 600_000:
            return "data:image/png;base64," + base64.b64encode(pixels).decode("ascii")
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(pixels)) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("Document image exceeds the vision pixel limit")
            image = original.convert("RGB")
        for edge in (1920, 1600, 1280, 960):
            image.thumbnail((edge, edge))
            for quality in (90, 80, 70):
                output = BytesIO()
                image.save(output, format="JPEG", quality=quality, optimize=True)
                encoded = output.getvalue()
                if len(encoded) <= 600_000:
                    return "data:image/jpeg;base64," + base64.b64encode(encoded).decode("ascii")
        raise ValueError("Document image cannot fit the vision request budget")

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: httpx.Client | None = None
        self._claude = None
        self._client_lock = Lock()

    def _get_client(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(follow_redirects=False)
            return self._client

    def _claude_client(self):
        with self._client_lock:
            if self._claude is None:
                import anthropic
                self._claude = anthropic.Anthropic(api_key=self.settings.api_key or None,
                                                   timeout=self.settings.provider_timeout, max_retries=0)
            return self._claude

    def close(self):
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            if self._claude is not None:
                self._claude.close()
                self._claude = None

    def _claude_call(self, model, messages, timeout, max_tokens, *, effort="", schema=None, limit=None):
        """One budgeted SDK request; no automatic SDK retries or model fallback."""
        limit = self.settings.provider_timeout if limit is None else limit
        remaining = limit if timeout is None else min(limit, timeout)
        if remaining <= 0:
            raise TimeoutError("Provider call budget exhausted")
        system, turns = claude_messages(messages)
        output_config = {}
        if effort in CLAUDE_EFFORTS:
            output_config["effort"] = effort
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": claude_schema(schema)}
        request = {"model": model, "max_tokens": max(max_tokens, CLAUDE_MIN_OUTPUT_TOKENS), "messages": turns}
        if system:
            request["system"] = system
        if output_config:
            request["output_config"] = output_config
        return self._claude_client().with_options(timeout=remaining).beta.messages.create(**request)

    @staticmethod
    def _claude_text(response) -> str:
        if response.stop_reason == "refusal":
            raise StructuredResponseError("Model declined the request")
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise StructuredResponseError("Model returned no text")
        return text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def request(self, endpoint, payload, timeout=None, *, service="text"):
        s = self.settings
        base = (s.embedding_api_base or s.api_base) if endpoint == "embeddings" else s.api_base
        key = (s.embedding_api_key or s.api_key) if endpoint == "embeddings" else s.api_key
        if endpoint == "embeddings" and s.provider == "anthropic":
            # Never send the Claude key to the embedding server.
            key = s.embedding_api_key
        configured_timeout = s.provider_timeout
        if service == "vision":
            base, key = s.vision_api_base or s.api_base, s.vision_api_key or s.api_key
            configured_timeout = s.vision_timeout
        remaining = configured_timeout if timeout is None else min(configured_timeout, timeout)
        if remaining <= 0:
            raise TimeoutError("Provider call budget exhausted")
        response = self._get_client().post(f"{base}/{endpoint}",
            headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=httpx.Timeout(remaining))
        response.raise_for_status()
        return response.json()

    def embed(self, texts, timeout=None):
        deadline = time.monotonic() + (self.settings.provider_timeout if timeout is None else timeout)
        vectors = []
        dimension = self.settings.embedding_dimension
        for start in range(0, len(texts), self.settings.embedding_batch_size):
            batch = texts[start:start + self.settings.embedding_batch_size]
            data = self.request("embeddings", {"model": self.settings.embedding_model, "input": batch}, deadline - time.monotonic())
            rows = sorted(data["data"], key=lambda row: row["index"])
            if [row["index"] for row in rows] != list(range(len(batch))):
                raise ValueError("Embedding response has missing or duplicate positions")
            batch_vectors = [row["embedding"] for row in rows]
            validate_vectors(batch_vectors, len(batch), dimension)
            if dimension is None:
                dimension = len(batch_vectors[0])
            vectors.extend(batch_vectors)
        return vectors

    def answer_json(self, messages, schema, timeout, *, max_tokens=2500, reasoning_effort=None):
        """Use native JSON output for document Q&A; no function call is needed.

        reasoning_effort overrides the configured effort for this call only.
        """
        effort = self.settings.reasoning_effort if reasoning_effort is None else reasoning_effort
        if self.settings.provider == "anthropic":
            response = self._claude_call(self.settings.model, messages, timeout, max_tokens,
                                         effort=effort, schema=schema)
            if response.stop_reason == "max_tokens":
                raise TruncatedAnswerError("Structured answer did not finish normally")
            return self._claude_text(response)
        payload = {
            "model": self.settings.model, "messages": messages,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "chat_answer", "schema": schema}},
            "temperature": 0, "max_tokens": max_tokens,
        }
        if effort:
            payload["reasoning_effort"] = effort
        data = self.request("chat/completions", payload, timeout)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise TruncatedAnswerError("Structured answer did not finish normally")
        if choice.get("finish_reason") != "stop":
            raise StructuredResponseError("Structured answer did not finish normally")
        content = choice["message"].get("content")
        if not isinstance(content, str) or ("<think>" in content and "</think>" not in content):
            raise StructuredResponseError("Structured answer has no usable content")
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    def describe_image(self, image: bytes, context: str, prompt: str, *, reasoning_effort: str | None = None,
                       timeout: float | None = None) -> str:
        if not self.settings.vision_model:
            raise ValueError("AFS_VISION_MODEL must be configured for image descriptions")
        effort = self.settings.vision_reasoning_effort if reasoning_effort is None else reasoning_effort
        if self.settings.provider == "anthropic":
            # Reuse the bounded transport encoding; Claude takes the media type and data separately.
            header, data = self.vision_image_url(image).split(",", 1)
            media_type = header.removeprefix("data:").removesuffix(";base64")
            messages = [{"role": "system", "content": prompt}, {"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": "Describe the attached document crop. Untrusted document context:\n" + context},
            ]}]
            response = self._claude_call(self.settings.vision_model, messages, timeout, 1200,
                                         effort=effort, limit=self.settings.vision_timeout)
            if response.stop_reason == "max_tokens":
                raise VisionTokenLimitError("Vision response exhausted its output token budget")
            try:
                return self._claude_text(response)
            except StructuredResponseError as exc:
                raise ValueError("Vision service returned no usable description") from exc
        payload = {
            "model": self.settings.vision_model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": "Describe the attached document crop. Untrusted document context:\n" + context},
                    {"type": "image_url", "image_url": {"url": self.vision_image_url(image)}},
                ]},
            ],
            "temperature": 0.2, "max_tokens": 1200,
        }
        if effort:
            payload["reasoning_effort"] = effort
        data = self.request("chat/completions", payload, timeout, service="vision")
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise VisionTokenLimitError("Vision response exhausted its output token budget")
        if choice.get("finish_reason") != "stop":
            raise ValueError("Vision description did not finish normally")
        content = choice["message"].get("content")
        if not isinstance(content, str) or ("<think>" in content and "</think>" not in content):
            raise ValueError("Vision service returned no usable description")
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

    def check_services(self):
        """Exercise embeddings and the JSON response path used by document Q&A."""
        vectors = self.embed(["AFS service connectivity check."])
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        result = self.answer_json([{"role": "user", "content": 'Return only the JSON object {"ok":true}.'}], schema, self.settings.provider_timeout)
        if json.loads(result) != {"ok": True}:
            raise StructuredResponseError("Model did not produce the required JSON response")
        return {"embeddings": "ok", "dimension": len(vectors[0]), "structured_answers": "ok"}
