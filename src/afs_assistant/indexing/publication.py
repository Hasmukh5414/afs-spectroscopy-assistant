"""Normalized JSON adapter. Actual format adapters must preserve the same contracts."""
import hashlib
import json
from pathlib import Path
from afs_assistant.core.models import Corpus
from afs_assistant.core.embeddings import validate_vectors


def ingest(path: Path, store, gateway=None):
    if path.stat().st_size > 5_000_000:
        raise ValueError("Normalized corpus exceeds 5 MB")
    corpus = Corpus.model_validate_json(path.read_text())
    ids = [item.evidence_id for item in corpus.items]
    if len(ids) != len(set(ids)):
        raise ValueError("Evidence IDs must be unique within a release")
    vectors = []
    model = ""
    if gateway:
        if corpus.items:
            vectors = gateway.embed([item.text for item in corpus.items])
            validate_vectors(vectors, len(corpus.items))
        model = gateway.settings.embedding_model
    canonical = json.dumps(corpus.model_dump(), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((canonical + "\n" + model).encode()).hexdigest()
    store.publish(corpus.release, digest, canonical, model, vectors)
    return {"release": corpus.release, "items": len(ids), "semantic_index": bool(vectors)}

