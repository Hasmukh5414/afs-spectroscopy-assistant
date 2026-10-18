"""Separate extraction, chunk and vector caches with explicit processing keys."""
import json
from pathlib import Path

from afs_assistant.core.embeddings import validate_vectors
from afs_assistant.core.models import ChunkBatch, ExtractedDocument, ImageDescription


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


class MaterialCache:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def artifact_directory(self, source_hash: str, processing_key: str) -> Path:
        return self.data_dir / "processed" / source_hash / processing_key

    def load_document(self, source_hash: str, processing_key: str) -> ExtractedDocument | None:
        directory = self.artifact_directory(source_hash, processing_key)
        path = directory / "material.json"
        if not path.exists():
            return None
        document = ExtractedDocument.model_validate_json(path.read_text())
        if document.source.sha256 != source_hash or document.processing_key != processing_key:
            raise ValueError("Extraction cache identity mismatch")
        if document.source.format == "pdf" and not (directory / "docling.json").exists():
            raise ValueError("Extraction cache is missing its canonical document")
        images = list(document.page_images.values()) + [element.image for element in document.elements if element.image]
        for image in images:
            relative = Path(image)
            if relative.is_absolute() or ".." in relative.parts or not (directory / relative).is_file():
                raise ValueError("Extraction cache is missing a retained image")
        return document

    def save_document(self, document: ExtractedDocument) -> None:
        directory = self.artifact_directory(document.source.sha256, document.processing_key)
        write_json(directory / "material.json", document.model_dump(mode="json"))

    def load_chunks(self, key: str) -> ChunkBatch | None:
        path = self.data_dir / "cache" / "chunks" / (key + ".json")
        return ChunkBatch.model_validate_json(path.read_text()) if path.exists() else None

    def save_chunks(self, key: str, batch: ChunkBatch) -> None:
        write_json(self.data_dir / "cache" / "chunks" / (key + ".json"), batch.model_dump(mode="json"))

    def load_image_description(self, key: str) -> ImageDescription | None:
        path = self.data_dir / "cache" / "image_descriptions" / (key + ".json")
        if not path.exists():
            return None
        value = ImageDescription.model_validate_json(path.read_text())
        if value.cache_key != key:
            raise ValueError("Image description cache identity mismatch")
        return value

    def save_image_description(self, description: ImageDescription) -> None:
        write_json(self.data_dir / "cache" / "image_descriptions" / (description.cache_key + ".json"), description.model_dump(mode="json"))

    def load_vectors(self, key: str, ids: list[str], dimension: int | None):
        path = self.data_dir / "cache" / "vectors" / (key + ".json")
        if not path.exists():
            return None
        data = json.loads(path.read_text())
        if data["evidence_ids"] != ids:
            raise ValueError("Vector cache does not match the chunk identities")
        validate_vectors(data["vectors"], len(ids), dimension)
        return data["vectors"]

    def save_vectors(self, key: str, ids: list[str], vectors) -> None:
        validate_vectors(vectors, len(ids))
        write_json(self.data_dir / "cache" / "vectors" / (key + ".json"), {"evidence_ids": ids, "vectors": vectors})
