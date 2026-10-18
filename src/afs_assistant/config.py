from dataclasses import dataclass, field
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database: Path = Path("var/afs.sqlite3")
    mode: str = "demo"
    # Text and vision service: "openai" (any OpenAI-compatible endpoint, e.g. Ollama) or "anthropic" (Claude SDK).
    # Embeddings always use the OpenAI-compatible embedding endpoint.
    provider: str = "openai"
    api_base: str = "https://api.openai.com/v1"
    api_key: str = field(default="", repr=False)
    model: str = ""
    embedding_api_base: str = ""
    embedding_api_key: str = field(default="", repr=False)
    embedding_model: str = ""
    embedding_dimension: int | None = None
    embedding_batch_size: int = 16
    provider_timeout: float = 60
    reasoning_effort: str = ""
    # Effort for writing the final answer only; empty uses reasoning_effort.
    answer_reasoning_effort: str = ""
    vision_api_base: str = ""
    vision_api_key: str = field(default="", repr=False)
    vision_model: str = ""
    vision_reasoning_effort: str = ""
    vision_timeout: float = 180
    vision_workers: int = 1
    data_dir: Path = Path("data")
    chunk_size: int = 128
    chunking_strategy: str = "section_aware"
    do_ocr: bool = False
    image_scale: float = 1.5
    candidate_limit: int = 30
    rrf_k: int = 60
    min_similarity: float = 0.4
    max_tool_calls: int = 12
    run_timeout: int = 300
    max_model_calls: int = 8
    evidence_byte_budget: int = 24000
    background_answers: bool = True

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env"):
        # AFS owns its configuration. Shell values override the selected local file.
        root = Path(os.environ.get("AFS_PROJECT_ROOT", str(PROJECT_ROOT))).expanduser().resolve()
        selected = Path(env_file).expanduser() if env_file is not None else None
        if selected is not None and not selected.is_absolute():
            selected = root / selected
        values = dict(dotenv_values(selected, interpolate=False)) if selected is not None else {}
        values.update(os.environ)
        def get(name, default=""):
            value = values.get(name)
            return default if value is None else value

        def local_path(value):
            path = Path(value).expanduser()
            return path if path.is_absolute() else root / path

        value = cls(
            database=local_path(get("AFS_DATABASE", "var/afs.sqlite3")),
            mode=get("AFS_MODE", "demo"),
            provider=get("AFS_PROVIDER", "openai").strip().lower() or "openai",
            api_base=get("AFS_API_BASE", "https://api.openai.com/v1").rstrip("/"),
            api_key=get("AFS_API_KEY"), model=get("AFS_MODEL"),
            embedding_api_base=get("AFS_EMBEDDING_API_BASE").rstrip("/"),
            embedding_api_key=get("AFS_EMBEDDING_API_KEY"),
            embedding_model=get("AFS_EMBEDDING_MODEL"),
            embedding_dimension=int(get("AFS_EMBEDDING_DIMENSION")) if get("AFS_EMBEDDING_DIMENSION") else None,
            embedding_batch_size=int(get("AFS_EMBEDDING_BATCH_SIZE", "16")),
            provider_timeout=float(get("AFS_PROVIDER_TIMEOUT", "60")),
            reasoning_effort=get("AFS_REASONING_EFFORT"),
            answer_reasoning_effort=get("AFS_ANSWER_REASONING_EFFORT"),
            vision_api_base=get("AFS_VISION_API_BASE").rstrip("/"),
            vision_api_key=get("AFS_VISION_API_KEY"),
            vision_model=get("AFS_VISION_MODEL"),
            vision_reasoning_effort=get("AFS_VISION_REASONING_EFFORT"),
            vision_timeout=float(get("AFS_VISION_TIMEOUT", "180")),
            vision_workers=int(get("AFS_VISION_WORKERS", "1")),
            data_dir=local_path(get("AFS_DATA_DIR", "data")),
            chunk_size=int(get("AFS_CHUNK_SIZE", "128")),
            chunking_strategy=get("AFS_CHUNKING_STRATEGY", "section_aware"),
            do_ocr=get("AFS_DO_OCR", "false").lower() == "true",
            image_scale=float(get("AFS_IMAGE_SCALE", "1.5")),
            candidate_limit=int(get("AFS_CANDIDATE_LIMIT", "30")),
            rrf_k=int(get("AFS_RRF_K", "60")),
            min_similarity=float(get("AFS_MIN_SIMILARITY", "0.4")),
            max_tool_calls=int(get("AFS_MAX_TOOL_CALLS", "12")),
            run_timeout=int(get("AFS_RUN_TIMEOUT", "300")),
            max_model_calls=int(get("AFS_MAX_MODEL_CALLS", "8")),
            evidence_byte_budget=int(get("AFS_EVIDENCE_BYTE_BUDGET", "24000")),
            background_answers=get("AFS_BACKGROUND_ANSWERS", "true").lower() == "true",
        )
        if value.mode not in {"demo", "openai"}:
            raise ValueError("AFS_MODE must be demo or openai")
        if value.provider not in {"openai", "anthropic"}:
            raise ValueError("AFS_PROVIDER must be openai or anthropic")
        if value.provider == "anthropic" and value.mode == "openai" and not (
                value.embedding_api_base and value.embedding_api_key):
            # Claude has no embedding endpoint; never send embeddings or the Claude key to the text-service URL.
            raise ValueError("AFS_PROVIDER=anthropic requires AFS_EMBEDDING_API_BASE and AFS_EMBEDDING_API_KEY (for example the local Ollama URL)")
        if not 1 <= value.max_tool_calls <= 12 or not 1 <= value.run_timeout <= 300:
            raise ValueError("Tool limit must be 1..12; timeout must be 1..300 seconds")
        if not 4 <= value.max_model_calls <= 20 or not 4000 <= value.evidence_byte_budget <= 64000:
            raise ValueError("Model call limit must be 4..20; evidence byte budget must be 4000..64000")
        if not 1 <= value.embedding_batch_size <= 128 or not 0 < value.provider_timeout <= 300:
            raise ValueError("Embedding batch size must be 1..128; provider timeout must be positive and at most 300 seconds")
        if value.embedding_dimension is not None and value.embedding_dimension < 1:
            raise ValueError("Embedding dimension must be positive when specified")
        if not 0 < value.vision_timeout <= 300:
            raise ValueError("Vision timeout must be positive and at most 300 seconds")
        if not 1 <= value.vision_workers <= 4:
            raise ValueError("Vision workers must be 1..4")
        if not 32 <= value.chunk_size <= 2000 or value.chunking_strategy not in {"sentence", "section_aware"}:
            raise ValueError("Chunk size must be 32..2000; strategy must be sentence or section_aware")
        if get("AFS_DO_OCR", "false").lower() not in {"true", "false"}:
            raise ValueError("AFS_DO_OCR must be true or false")
        if not 0.5 <= value.image_scale <= 3 or not 1 <= value.candidate_limit <= 100 or not 1 <= value.rrf_k <= 1000:
            raise ValueError("Invalid image scale or retrieval limits")
        if not -1 <= value.min_similarity <= 1:
            raise ValueError("Minimum similarity must be between -1 and 1")
        for base in [value.api_base, value.embedding_api_base or value.api_base, value.vision_api_base or value.api_base]:
            parsed = urlsplit(base)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Provider base URLs must use HTTP(S), without embedded credentials, query or fragment")
        if value.mode == "openai" and not all([value.api_key, value.model, value.embedding_model]):
            raise ValueError("OpenAI mode requires API key, model and embedding model")
        return value

