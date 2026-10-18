"""Coordinate source snapshots, Docling, chunks, vectors and atomic publication."""
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any

from afs_assistant.config import Settings
from afs_assistant.core.embeddings import validate_vectors
from afs_assistant.core.fingerprints import fingerprint
from afs_assistant.core.gateway import Gateway
from afs_assistant.core.lexical_index import build_index
from afs_assistant.core.models import Corpus
from afs_assistant.core.storage import Store
from .chunking import DocumentChunker
from .extractor import DoclingExtractor
from .ingestion import load_materials
from .image_processor import IMAGE_CHUNK_VERSION, ImageProcessor
from .material_cache import MaterialCache, write_json


class EvidenceBuilder:
    def __init__(self, settings: Settings, store: Store, gateway: Gateway | None = None, extractor=None, chunker=None):
        self.settings = settings
        self.store = store
        self.gateway = gateway
        self.extractor = extractor or DoclingExtractor(settings)
        self.chunker = chunker or DocumentChunker(settings)
        self.cache = MaterialCache(settings.data_dir)

    def process_materials(self, receipt: Path, release: str, generate_embeddings: bool = False,
                          describe_images: bool = False, publish_release: bool = True) -> dict:
        # Validate release syntax before using it as a path.
        Corpus(release=release, items=[])
        if generate_embeddings and self.gateway is None:
            raise ValueError("Embedding preparation requires an explicitly configured model service")
        if describe_images and (self.gateway is None or not self.settings.vision_model):
            raise ValueError("Image descriptions require a configured vision model and gateway")
        image_processor = ImageProcessor(self.settings, self.cache, self.gateway) if describe_images and self.gateway else None
        materials = load_materials(receipt, self.settings.data_dir)
        started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        report_path = self.settings.data_dir / "runs" / release / (started + ".json")
        report: dict[str, Any] = {"release": release, "status": "processing", "sources": [], "chunks": 0,
                  "documents_cached": 0, "chunk_sets_cached": 0, "embeddings_cached": 0}
        write_json(report_path, report)
        items = []
        contexts = []
        sources = []
        vectors = []
        references = []
        coverage = []
        processing = {"chunker": self.chunker.cache_identity, "chunk_size": self.settings.chunk_size,
            "chunking_strategy": self.settings.chunking_strategy, "tokenizer": "spacy-xx-blank",
            "spacy": version("spacy"), "embedding_model": self.settings.embedding_model if generate_embeddings else "",
            "embedding_dimension": self.settings.embedding_dimension if generate_embeddings else None}
        if image_processor:
            processing["vision"] = image_processor.profile
            processing["image_enrichment"] = IMAGE_CHUNK_VERSION
            processing["vision_fallback"] = image_processor.fallback_policy
        for material in materials:
            source = material.source
            state: dict[str, Any] = {"source_id": source.source_id, "status": "extracting"}
            report["sources"].append(state)
            write_json(report_path, report)
            try:
                parse_key = self.extractor.processing_key(source.sha256)
                document = self.cache.load_document(source.sha256, parse_key)
                if document is None:
                    directory = self.cache.artifact_directory(source.sha256, parse_key)
                    document = self.extractor.extract(material, directory)
                    self.cache.save_document(document)
                else:
                    report["documents_cached"] += 1
                # Source issues and admission metadata may change independently of its bytes.
                document.source = source.model_copy(update={"artifact_key": source.sha256 + "/" + parse_key})
                chunk_key = fingerprint({"document": parse_key, "source": document.source.model_dump(), "settings": {
                    "chunker": self.chunker.cache_identity, "size": self.settings.chunk_size,
                    "strategy": self.settings.chunking_strategy, "spacy": version("spacy")}})
                state["status"] = "chunking"
                write_json(report_path, report)
                batch = self.cache.load_chunks(chunk_key)
                if batch is None:
                    batch = self.chunker.process_material(document)
                    self.cache.save_chunks(chunk_key, batch)
                else:
                    report["chunk_sets_cached"] += 1
                if image_processor:
                    state["status"] = "describing_images"
                    def progress(counts: dict) -> None:
                        state["images"] = counts
                        write_json(report_path, report)
                    batch = image_processor.enrich(batch, document, progress, self.chunker.count)
                    # Description bytes and provenance participate in vector-cache identity.
                    chunk_key = fingerprint({"base_chunks": chunk_key, "vision": image_processor.profile,
                                             "items": [item.model_dump() for item in batch.items]})
                    self.cache.save_chunks(chunk_key, batch)
                if generate_embeddings:
                    state["status"] = "embedding"
                    write_json(report_path, report)
                    ids = [item.evidence_id for item in batch.items]
                    vector_key = fingerprint({"chunks": chunk_key, "model": self.settings.embedding_model,
                        "endpoint": self.settings.embedding_api_base or self.settings.api_base,
                        "dimension": self.settings.embedding_dimension})
                    source_vectors = self.cache.load_vectors(vector_key, ids, self.settings.embedding_dimension)
                    if source_vectors is None:
                        if self.gateway is None:
                            raise ValueError("Embedding service is not configured")
                        source_vectors = self.gateway.embed([item.text for item in batch.items])
                        validate_vectors(source_vectors, len(batch.items), self.settings.embedding_dimension)
                        self.cache.save_vectors(vector_key, ids, source_vectors)
                    else:
                        report["embeddings_cached"] += len(source_vectors)
                    validate_vectors(source_vectors, len(batch.items), self.settings.embedding_dimension)
                    vectors.extend(source_vectors)
                items.extend(batch.items)
                contexts.extend(batch.contexts)
                sources.append(document.source)
                references.extend(document.references)
                coverage.extend(document.coverage)
                report["chunks"] += len(batch.items)
                state.update(status="extracted_requires_review", chunks=len(batch.items), pages=document.page_count)
            except KeyboardInterrupt:
                state.update(error_stage=state["status"], status="interrupted", error_type="KeyboardInterrupt")
                report["status"] = "interrupted"
                write_json(report_path, report)
                raise
            except Exception as exc:
                state.update(error_stage=state["status"], status="failed", error_type=type(exc).__name__)
            write_json(report_path, report)
        if any(source["status"] == "failed" for source in report["sources"]):
            report["status"] = "failed"
            write_json(report_path, report)
            return report
        if image_processor:
            report["images"] = {key: sum(source.get("images", {}).get(key, 0) for source in report["sources"])
                                for key in ("total", "completed", "generated", "cached")}
        # Reference metadata is separate from searchable scientific evidence.
        from .references import resolve_supplied_references
        references = resolve_supplied_references(references, sources)
        processing["coverage"] = coverage
        corpus = Corpus(release=release, items=items, contexts=contexts, sources=sources, processing=processing, references=references)
        validator = getattr(self.extractor, "validate_corpus", None)
        if validator is not None:
            validator(corpus)
        if generate_embeddings:
            validate_vectors(vectors, len(items), self.settings.embedding_dimension)
        model = self.settings.embedding_model if generate_embeddings else ""
        payload = corpus.model_dump_json()
        # All sources succeed before this immutable, transactional publication.
        build_index(tuple(item.text for item in items if item.review_status != "disputed"))
        digest = fingerprint({"corpus": corpus.model_dump(mode="json"), "model": model, "vectors": vectors})
        if publish_release:
            self.store.publish(release, digest, payload, model, vectors)
        report.update(status="ready_for_retrieval" if publish_release else "prepared",
                      published=publish_release, semantic_index=generate_embeddings,
                      review="extracted; exact specifications and source conflicts remain unreviewed")
        write_json(self.settings.data_dir / "releases" / release / "corpus.json", corpus.model_dump(mode="json"))
        write_json(report_path, report)
        return report
