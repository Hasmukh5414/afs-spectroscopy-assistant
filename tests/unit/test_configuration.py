from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any, cast
import unittest
from unittest.mock import patch

import anthropic
import httpx
import httpx2
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from afs_assistant.api import chat_failure_code
from afs_assistant.config import Settings
from afs_assistant.core.gateway import Gateway, StructuredResponseError, VisionTokenLimitError, claude_schema
from afs_assistant.core.models import Assessment, ChatAnswer, EvidenceItem
from afs_assistant.core.tool_schema import tool_parameters
from afs_assistant.workflow.agent import AgentResponder, InvestigationBudget, with_timeout


class ConfigurationTests(unittest.TestCase):
    def test_local_config_shell_precedence_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("AFS_MODE=openai\nAFS_API_KEY=private-key\nAFS_MODEL=file-model\nAFS_EMBEDDING_MODEL=emb\nAFS_EMBEDDING_DIMENSION=1024\n")
            with patch.dict(os.environ, {"AFS_MODEL": "shell-model"}, clear=True):
                settings = Settings.from_env(path)
            self.assertEqual(settings.model, "shell-model")
            self.assertEqual(settings.embedding_dimension, 1024)
            self.assertNotIn("private-key", repr(settings))

    def test_invalid_embedding_settings_and_credential_urls_rejected(self):
        for variable, value in [("AFS_EMBEDDING_DIMENSION", "0"), ("AFS_EMBEDDING_BATCH_SIZE", "0"),
                                ("AFS_PROVIDER_TIMEOUT", "nan"), ("AFS_API_BASE", "https://user:secret@example.com/v1"),
                                ("AFS_VISION_TIMEOUT", "nan"), ("AFS_VISION_WORKERS", "5"),
                                ("AFS_VISION_API_BASE", "https://user:secret@example.com/v1")]:
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: value}, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env(None)


class GatewayTests(unittest.TestCase):
    def test_document_answer_uses_json_schema_without_function_tools(self):
        schema = {"type": "object", "properties": {"parts": {"type": "array"}}}
        content = '{"parts":[]}'
        with patch.object(Gateway, "request", return_value={"choices": [{"finish_reason": "stop", "message": {"content": content, "tool_calls": None}}]}) as request:
            result = Gateway(Settings(model="text", reasoning_effort="none")).answer_json([], schema, 30)
            self.assertEqual(result, content)
            payload = request.call_args.args[1]
            self.assertEqual(payload["response_format"]["json_schema"]["schema"], schema)
            self.assertEqual(payload["reasoning_effort"], "none")
            self.assertNotIn("tools", payload)
            self.assertNotIn("tool_choice", payload)
        for reason, text in [("length", content), ("stop", None), ("stop", "<think>unfinished")]:
            with patch.object(Gateway, "request", return_value={"choices": [{"finish_reason": reason, "message": {"content": text}}]}):
                with self.assertRaises(StructuredResponseError):
                    Gateway(Settings()).answer_json([], schema, 30)

    def test_vision_route_pixels_context_and_thinking_cleanup(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": "<think>private reasoning</think>A spectrum with a labeled absorbance axis and overlapping curves."}}]})
        client = httpx.Client
        settings = Settings(api_base="https://text.example/v1", api_key="text-key",
            vision_api_base="https://vision.example/v1", vision_api_key="vision-secret", vision_model="vision-a",
            vision_reasoning_effort="none", vision_timeout=180, provider_timeout=60)
        with patch("afs_assistant.core.gateway.httpx.Client", side_effect=lambda **kw: client(transport=httpx.MockTransport(respond), **kw)):
            with Gateway(settings) as gateway:
                text = gateway.describe_image(b"png pixels", "Caption: absorbance", "Describe visible content.")
        self.assertNotIn("private reasoning", text)
        self.assertNotIn("vision-secret", repr(settings))
        request = seen[0]
        self.assertEqual(request.url.host, "vision.example")
        self.assertEqual(request.headers["authorization"], "Bearer vision-secret")
        self.assertEqual(request.extensions["timeout"]["read"], 180)
        payload = json.loads(request.content)
        self.assertEqual(payload["model"], "vision-a")
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertIn("Caption: absorbance", payload["messages"][1]["content"][0]["text"])
        self.assertEqual(payload["messages"][1]["content"][1]["image_url"]["url"], "data:image/png;base64,cG5nIHBpeGVscw==")
        self.assertNotIn("tools", payload)

    def test_vision_rejects_truncated_or_missing_content(self):
        for reason, content in [("length", "A plausible but truncated description"), ("stop", None),
                                ("stop", "<think>unfinished reasoning")]:
            with self.subTest(reason=reason, content=content), patch.object(Gateway, "request", return_value={
                    "choices": [{"finish_reason": reason, "message": {"content": content}}]}):
                with self.assertRaises(ValueError):
                    Gateway(Settings(vision_model="vision-a")).describe_image(b"image", "context", "prompt")

    def test_nested_tool_contracts_are_self_contained(self):
        for model in (Assessment, ChatAnswer):
            schema = tool_parameters(model)
            self.assertNotIn('"$ref"', json.dumps(schema))
            self.assertNotIn('"$defs"', json.dumps(schema))
        requirement = tool_parameters(Assessment)["properties"]["requirements"]["items"]
        self.assertEqual(requirement["type"], "object")
        self.assertEqual(requirement["properties"]["user_quote"]["type"], "string")

    def test_separate_routes_keys_batch_order_and_reasoning(self):
        seen = []
        def respond(request):
            seen.append(request)
            payload = json.loads(request.content)
            if request.url.path.endswith("embeddings"):
                # Providers may return rows out of order, within each batch.
                return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(text), 1.0]}
                    for i, text in reversed(list(enumerate(payload["input"])))]})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
        transport = httpx.MockTransport(respond)
        client = httpx.Client
        settings = Settings(api_base="https://text.example/v1", api_key="text-key", model="text",
                            embedding_api_base="https://embed.example/v1", embedding_api_key="embed-key",
                            embedding_model="embedding", embedding_dimension=2, embedding_batch_size=2,
                            reasoning_effort="none")
        with patch("afs_assistant.core.gateway.httpx.Client", side_effect=lambda **kwargs: client(transport=transport, **kwargs)) as factory:
            with Gateway(settings) as gateway:
                self.assertEqual(gateway.embed(["1", "2", "3"]), [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]])
                gateway.answer_json([], {"type": "object"}, 3)
            self.assertEqual(factory.call_count, 1)
        self.assertEqual([request.url.host for request in seen], ["embed.example", "embed.example", "text.example"])
        self.assertEqual(seen[0].headers["authorization"], "Bearer embed-key")
        self.assertEqual(seen[2].headers["authorization"], "Bearer text-key")
        self.assertEqual(json.loads(seen[2].content)["reasoning_effort"], "none")

    def test_bad_vectors_do_not_reach_the_index(self):
        for rows in [[], [{"index": 1, "embedding": [1, 2]}],
                     [{"index": 0, "embedding": [1]}], [{"index": 0, "embedding": [0, 0]}],
                     [{"index": 0, "embedding": [True, 1]}]]:
            with self.subTest(rows=rows), patch.object(Gateway, "request", return_value={"data": rows}):
                with self.assertRaises(ValueError):
                    Gateway(Settings(embedding_dimension=2)).embed(["test"])

    def test_empty_reasoning_is_omitted_and_expired_budget_rejected(self):
        with patch.object(Gateway, "request", return_value={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}) as request:
            Gateway(Settings()).answer_json([], {"type": "object"}, 1)
            self.assertNotIn("reasoning_effort", request.call_args.args[1])
        with self.assertRaises(TimeoutError):
            Gateway(Settings()).request("embeddings", {}, -1)


class FakeClaude:
    """Records Claude requests offline and returns a prepared response."""
    def __init__(self, response):
        self.response = response
        self.requests = []
        self.timeouts = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def with_options(self, timeout):
        self.timeouts.append(timeout)
        return self

    def create(self, **request):
        self.requests.append(request)
        return self.response

    def close(self):
        pass


def claude_response(text: str | None = '{"ok": true}', stop_reason="end_turn"):
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(stop_reason=stop_reason, content=content)


class ClaudeProviderTests(unittest.TestCase):
    settings = Settings(mode="openai", provider="anthropic", api_key="claude-key", model="claude-opus-5-5",
                        embedding_api_base="https://embed.example/v1", embedding_api_key="ollama",
                        embedding_model="embedding", reasoning_effort="medium", vision_model="claude-opus-5-5")

    def test_provider_setting_is_validated(self):
        base = {"AFS_MODE": "openai", "AFS_API_KEY": "k", "AFS_MODEL": "m", "AFS_EMBEDDING_MODEL": "e"}
        for extra in [{"AFS_PROVIDER": "gemini"}, {"AFS_PROVIDER": "anthropic"},
                      {"AFS_PROVIDER": "anthropic", "AFS_EMBEDDING_API_BASE": "http://localhost:11434/v1"}]:
            with self.subTest(extra=extra), patch.dict(os.environ, base | extra, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env(None)
        with patch.dict(os.environ, base | {"AFS_PROVIDER": "Anthropic", "AFS_EMBEDDING_API_BASE": "http://localhost:11434/v1",
                                            "AFS_EMBEDDING_API_KEY": "ollama"}, clear=True):
            self.assertEqual(Settings.from_env(None).provider, "anthropic")
        with patch.dict(os.environ, base, clear=True):
            self.assertEqual(Settings.from_env(None).provider, "openai")

    def test_schema_drops_unsupported_keywords_but_keeps_fields(self):
        schema = claude_schema(tool_parameters(ChatAnswer))
        text = json.dumps(schema)
        for keyword in ("minLength", "maxLength", "minItems", "maxItems", "pattern", '"default"'):
            self.assertNotIn(keyword, text)
        part = schema["properties"]["parts"]["items"]
        self.assertIn("parts", schema["required"])
        self.assertIn("evidence_ids", part["properties"])
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(part["additionalProperties"])
        # A field named like a schema keyword is data and must survive.
        evidence = claude_schema(tool_parameters(EvidenceItem))
        self.assertIn("title", evidence["properties"])

    def test_structured_request_shape(self):
        fake = FakeClaude(claude_response('{"parts": []}'))
        with patch.object(Gateway, "_claude_client", return_value=fake):
            result = Gateway(self.settings).answer_json([
                {"role": "system", "content": "Rules."}, {"role": "user", "content": "Question?"},
                {"role": "user", "content": "Passages."}], tool_parameters(ChatAnswer), 30, max_tokens=2500)
        self.assertEqual(result, '{"parts": []}')
        request = fake.requests[0]
        self.assertEqual(request["model"], "claude-opus-5-5")
        self.assertEqual(request["system"], "Rules.")
        self.assertEqual(request["messages"], [{"role": "user", "content": "Question?\n\nPassages."}])
        self.assertEqual(request["output_config"]["effort"], "medium")
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("fallbacks", request)
        self.assertNotIn("betas", request)
        self.assertGreaterEqual(request["max_tokens"], 16000)
        self.assertNotIn("temperature", request)
        self.assertNotIn("thinking", request)
        self.assertEqual(fake.timeouts, [30])

    def test_ollama_effort_value_is_not_sent_to_claude(self):
        fake = FakeClaude(claude_response())
        with patch.object(Gateway, "_claude_client", return_value=fake):
            Gateway(replace(self.settings, reasoning_effort="none")).answer_json([{"role": "user", "content": "x"}], {"type": "object"}, 30)
        self.assertNotIn("effort", fake.requests[0]["output_config"])

    def test_cut_off_answers_are_a_distinct_error_and_claude_has_room(self):
        from afs_assistant.core.gateway import TruncatedAnswerError
        fake = FakeClaude(claude_response(stop_reason="max_tokens"))
        with patch.object(Gateway, "_claude_client", return_value=fake):
            with self.assertRaises(TruncatedAnswerError):
                Gateway(self.settings).answer_json([{"role": "user", "content": "x"}], {"type": "object"}, 30)
        self.assertEqual(fake.requests[0]["max_tokens"], 20000)
        for reason, error in [("length", TruncatedAnswerError), ("content_filter", StructuredResponseError)]:
            reply = {"choices": [{"finish_reason": reason, "message": {"content": "{}"}}]}
            with self.subTest(reason=reason), patch.object(Gateway, "request", return_value=reply):
                with self.assertRaises(error) as caught:
                    Gateway(Settings(model="text")).answer_json([], {"type": "object"}, 30)
                self.assertEqual(isinstance(caught.exception, TruncatedAnswerError), reason == "length")

    def test_per_call_effort_overrides_configured_effort(self):
        fake = FakeClaude(claude_response())
        with patch.object(Gateway, "_claude_client", return_value=fake):
            gateway = Gateway(self.settings)
            gateway.answer_json([{"role": "user", "content": "x"}], {"type": "object"}, 30, reasoning_effort="low")
            gateway.answer_json([{"role": "user", "content": "x"}], {"type": "object"}, 30)
        self.assertEqual([r["output_config"]["effort"] for r in fake.requests], ["low", "medium"])

    def test_haiku_requests_also_omit_refusal_fallback(self):
        fake = FakeClaude(claude_response())
        with patch.object(Gateway, "_claude_client", return_value=fake):
            Gateway(replace(self.settings, model="claude-haiku-5-5")).answer_json([{"role": "user", "content": "x"}], {"type": "object"}, 30)
        self.assertNotIn("fallbacks", fake.requests[0])
        self.assertNotIn("betas", fake.requests[0])

    def test_refusal_truncation_and_empty_output_fail_clearly(self):
        for response in [claude_response(stop_reason="refusal"), claude_response(stop_reason="max_tokens"), claude_response(text=None)]:
            with self.subTest(stop=response.stop_reason), patch.object(Gateway, "_claude_client", return_value=FakeClaude(response)):
                with self.assertRaises(StructuredResponseError):
                    Gateway(self.settings).answer_json([{"role": "user", "content": "x"}], {"type": "object"}, 30)
        with patch.object(Gateway, "_claude_client", return_value=FakeClaude(claude_response(stop_reason="max_tokens"))):
            with self.assertRaises(VisionTokenLimitError):
                Gateway(self.settings).describe_image(b"png", "context", "prompt")

    def test_image_block_vision_timeout_and_embeddings_stay_on_ollama(self):
        fake = FakeClaude(claude_response("A labelled spectrum with two overlapping absorbance curves."))
        with patch.object(Gateway, "_claude_client", return_value=fake):
            Gateway(replace(self.settings, vision_timeout=50)).describe_image(
                b"png pixels", "Caption: absorbance", "Describe visible content.", timeout=200)
        content = fake.requests[0]["messages"][0]["content"]
        self.assertEqual(content[0]["source"], {"type": "base64", "media_type": "image/png", "data": "cG5nIHBpeGVscw=="})
        self.assertIn("Caption: absorbance", content[1]["text"])
        self.assertEqual(fake.timeouts, [50])
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.5]}]})
        client = httpx.Client
        with patch("afs_assistant.core.gateway.httpx.Client", side_effect=lambda **kw: client(transport=httpx.MockTransport(respond), **kw)), \
                patch.object(Gateway, "_claude_client", side_effect=AssertionError("Claude must not embed")):
            with Gateway(self.settings) as gateway:
                gateway.embed(["text"])
        self.assertEqual(seen[0].url.host, "embed.example")
        self.assertEqual(seen[0].headers["authorization"], "Bearer ollama")

    def test_sdk_server_error_is_one_request_without_hidden_retries(self):
        requests = []
        clients = []
        real_client = anthropic.Anthropic
        def respond(request):
            requests.append(request)
            return httpx2.Response(503, json={"type": "error", "error": {
                "type": "overloaded_error", "message": "Temporary overload"}})
        def create(**kwargs):
            self.assertEqual(kwargs["max_retries"], 0)
            client = real_client(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(respond)))
            clients.append(client)
            return client
        with patch("anthropic.Anthropic", side_effect=create):
            with Gateway(self.settings) as gateway:
                with self.assertRaises(anthropic.APIStatusError):
                    gateway.answer_json([{"role": "user", "content": "Question."}], {"type": "object"}, 5)
        self.assertEqual(len(requests), 1)
        self.assertNotIn("fallbacks", json.loads(requests[0].content))
        self.assertTrue(clients[0].is_closed())

    def test_provider_failure_codes(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        self.assertEqual(chat_failure_code(anthropic.APITimeoutError(request=request)), "timeout")
        self.assertEqual(chat_failure_code(anthropic.APIConnectionError(request=request)), "connection")
        self.assertEqual(chat_failure_code(anthropic.RateLimitError("limit", response=httpx2.Response(429, request=request), body=None)), "provider_rejected")

    def test_agent_builds_claude_model_with_fresh_timeout(self):
        responder = AgentResponder(None, replace(self.settings, provider_timeout=120), None, thread_id="t")
        model = responder.chat_model()
        assert isinstance(model, ChatAnthropic)
        self.assertEqual(model.model, "claude-opus-5-5")
        self.assertEqual(model.max_tokens, 20000)
        self.assertEqual(model.reasoning_effort, "medium")
        self.assertIsNone(model.temperature)
        vars(model)["_client"] = "client built with the old timeout"
        bounded = with_timeout(model, 30)
        assert isinstance(bounded, ChatAnthropic)
        self.assertEqual(bounded.default_request_timeout, 30)
        self.assertNotIn("_client", vars(bounded))
        self.assertEqual(model.default_request_timeout, 120)
        unsupported = AgentResponder(None, replace(self.settings, reasoning_effort="none"), None, thread_id="t")
        self.assertIsNone(unsupported.chat_model().reasoning_effort)
        openai = AgentResponder(None, replace(self.settings, provider="openai", reasoning_effort=""), Gateway(Settings()), thread_id="t")
        self.assertIsInstance(openai.chat_model(), ChatOpenAI)

    def test_grounding_uses_auto_tool_choice_for_claude_only(self):
        for provider, expected in [("anthropic", "auto"), ("openai", "required")]:
            with self.subTest(provider=provider):
                tools = SimpleNamespace(remaining=lambda: 100, calls=0, inspected={}, aliases={}, event=lambda event: None,
                    unread_passages=lambda: [], has_read_passage=lambda: False)
                owner = SimpleNamespace(settings=replace(self.settings, provider=provider), model_calls=0, agent_turns=0,
                                        reserved_calls=2, tools=tools)
                seen = []
                class Request:
                    model = ChatAnthropic.model_validate({"model": "claude-opus-5-5", "api_key": "k"})
                    def override(self, **changes):
                        seen.append(changes["tool_choice"])
                        return self
                tool_call = AIMessage(content="", tool_calls=[{"name": "search_knowledge", "args": {"query": "x"}, "id": "1"}])
                handler = cast(Any, lambda request: SimpleNamespace(result=[tool_call]))
                InvestigationBudget(owner).wrap_model_call(cast(Any, Request()), handler)
                self.assertEqual(seen, [expected])

    def test_last_turn_reads_unread_passages_when_only_table_rows_were_inspected(self):
        from afs_assistant.workflow.agent import AGENT_TURNS
        for has_text, unread, expected in [(False, ["E3"], "read_evidence"), (True, ["E3"], "auto"), (False, [], "auto")]:
            with self.subTest(has_text=has_text, unread=unread):
                tools = SimpleNamespace(remaining=lambda: 100, calls=2, inspected={"E1": {"table_row": {}}}, aliases={},
                    event=lambda event: None, unread_passages=lambda: unread, has_read_passage=lambda: has_text)
                owner = SimpleNamespace(settings=replace(self.settings, provider="openai"), model_calls=1,
                                        agent_turns=AGENT_TURNS - 1, reserved_calls=2, tools=tools)
                seen = []
                class Request:
                    model = ChatOpenAI.model_validate({"model": "m", "api_key": "k"})
                    messages = []
                    def override(self, **changes):
                        seen.append(changes["tool_choice"])
                        return self
                tool_call = AIMessage(content="", tool_calls=[{"name": "read_evidence", "args": {"reference": "E3"}, "id": "1"}])
                InvestigationBudget(owner).wrap_model_call(cast(Any, Request()), cast(Any, lambda request: SimpleNamespace(result=[tool_call])))
                self.assertEqual(seen[-1], expected)


if __name__ == "__main__":
    unittest.main()
