import hashlib
import json
from pathlib import Path

import pytest

from afs_assistant.config import Settings
from afs_assistant.core.gateway import Gateway
from afs_assistant.core.models import DocumentElement, ExtractedDocument, Provenance, SourceMaterial, SourceVersion
from afs_assistant.core.storage import Store
from afs_assistant.indexing.builder import EvidenceBuilder
from afs_assistant.indexing.chunking import DocumentChunker
from afs_assistant.indexing.ingestion import load_materials


def material(tmp_path: Path, source_id="test"):
    content = b"%PDF-1.4 test source " + source_id.encode()
    digest = hashlib.sha256(content).hexdigest()
    path = tmp_path / "originals" / digest / "test.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    source = SourceVersion(source_id=source_id, sha256=digest, title="Test technical source", uri="https://example.com/test.pdf",
        filename="test.pdf", kind="application", expected_pages=1, issue_ids=["Q01"])
    return SourceMaterial(source=source, path=path)


def receipt(tmp_path, materials):
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps([{"source_id": item.source.source_id, "title": item.source.title,
        "url": item.source.uri, "path": str(item.path), "sha256": item.source.sha256, "role": item.source.kind,
        "expected_pages": 1, "status": "acquired_unreviewed", "issues_from_specification": item.source.issue_ids} for item in materials]))
    return path


class FixtureExtractor:
    def __init__(self):
        self.calls = 0
        self.fail_source = ""

    def processing_key(self, source_hash):
        return "fixture-parser-v1"

    def extract(self, source_material, directory):
        self.calls += 1
        if source_material.source.source_id == self.fail_source:
            raise ValueError("Deliberate parser failure")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "docling.json").write_text("{}")
        return ExtractedDocument(source=source_material.source, parser_version="fixture", processing_key="fixture-parser-v1",
            markdown="Moisture powder evidence.", page_count=1,
            elements=[DocumentElement(element_id="#/texts/0", label="text", text="Moisture powder evidence.",
                                      provenance=[Provenance(page=1)])])


class FixtureGateway(Gateway):
    def __init__(self, settings):
        super().__init__(settings)
        self.calls = 0

    def embed(self, texts, timeout=None):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


def test_sections_rows_headers_notes_and_source_locations(tmp_path):
    doc = ExtractedDocument(source=material(tmp_path).source, parser_version="fixture", processing_key="test",
        markdown="", page_count=1, elements=[
            DocumentElement(element_id="h1", label="section_header", text="Probe A", heading_level=1, provenance=[Provenance(page=1)]),
            DocumentElement(element_id="p1", label="text", text="Use only for the stated matrix.", provenance=[Provenance(page=1)]),
            DocumentElement(element_id="t1", label="table", text="| Component | Limit |\n| --- | --- |\n| Shaft | 120 C |\n| Legs | 60 C |",
                notes="Ratings depend on seals.", provenance=[Provenance(page=1, bbox=(0, 100, 300, 200))]),
            DocumentElement(element_id="h2", label="section_header", text="Probe B", heading_level=1, provenance=[Provenance(page=1)]),
            DocumentElement(element_id="p2", label="text", text="A separate configuration.", provenance=[Provenance(page=1)]),
        ])
    batch = DocumentChunker(Settings(chunk_size=32)).process_material(doc)
    rows = [item for item in batch.items if item.element_type == "table_row"]
    assert len(rows) == 2
    assert all("Component | Limit" in row.text and "Ratings depend on seals." in row.text for row in rows)
    assert "120 C" in rows[0].text and "60 C" not in rows[0].text
    assert rows[0].provenance[0].bbox == (0, 100, 300, 200)
    assert all(item.issue_ids == ["Q01"] and not item.synthetic for item in batch.items)
    assert not any("Probe A" in item.text and "Probe B" in item.text for item in batch.items)
    assert all(item.parent_context_id for item in batch.items)
    assert [item.evidence_id for item in DocumentChunker(Settings(chunk_size=32)).process_material(doc).items] == [item.evidence_id for item in batch.items]


def test_docling_table_padding_is_removed_without_changing_cells(tmp_path):
    padded = "| Data      | DQ2       |\n|-----------|-----------|\n| An41      | 0.932     |\n| As41      | 0.917     |"
    doc = ExtractedDocument(source=material(tmp_path).source, parser_version="fixture", processing_key="test",
        markdown="", page_count=1, elements=[DocumentElement(element_id="t", label="table", text=padded, provenance=[Provenance(page=1)])])
    rows = DocumentChunker(Settings()).process_material(doc).items
    assert [row.text for row in rows] == ["| Data | DQ2 |\n|---|---|\n| An41 | 0.932 |", "| Data | DQ2 |\n|---|---|\n| As41 | 0.917 |"]


def test_builder_cache_separates_embedding_changes_from_parsing(tmp_path):
    input_receipt = receipt(tmp_path, [material(tmp_path)])
    settings = Settings(data_dir=tmp_path, embedding_model="model-a", embedding_dimension=2)
    store = Store(tmp_path / "db.sqlite3")
    extractor = FixtureExtractor()
    gateway = FixtureGateway(settings)
    builder = EvidenceBuilder(settings, store, gateway, extractor=extractor)
    first = builder.process_materials(input_receipt, "first", True)
    repeated = builder.process_materials(input_receipt, "first", True)
    assert first["status"] == "ready_for_retrieval"
    assert extractor.calls == 1 and gateway.calls == 1
    assert repeated["documents_cached"] == 1 and repeated["embeddings_cached"] > 0
    changed = Settings(data_dir=tmp_path, embedding_model="model-b", embedding_dimension=2)
    new_gateway = FixtureGateway(changed)
    report = EvidenceBuilder(changed, store, new_gateway, extractor=extractor).process_materials(input_receipt, "second", True)
    assert report["documents_cached"] == 1 and report["chunk_sets_cached"] == 1
    assert extractor.calls == 1 and new_gateway.calls == 1
    old_snapshot = store.corpus("first")
    new_snapshot = store.corpus("second")
    assert old_snapshot is not None and new_snapshot is not None
    assert old_snapshot["model"] == "model-a"
    assert new_snapshot["model"] == "model-b"


def test_failed_source_prevents_partial_release_publication(tmp_path):
    inputs = receipt(tmp_path, [material(tmp_path, "good"), material(tmp_path, "bad")])
    store = Store(tmp_path / "db.sqlite3")
    extractor = FixtureExtractor()
    extractor.fail_source = "bad"
    report = EvidenceBuilder(Settings(data_dir=tmp_path), store, extractor=extractor).process_materials(inputs, "failed")
    assert report["status"] == "failed"
    assert store.corpus() is None
    assert any(item["status"] == "failed" for item in report["sources"])


def test_modified_original_is_rejected_before_parsing(tmp_path):
    source = material(tmp_path)
    path = receipt(tmp_path, [source])
    source.path.write_bytes(b"%PDF-1.4 changed")
    with pytest.raises(ValueError, match="identity"):
        load_materials(path, tmp_path)


def test_captioned_and_uncaptioned_images_are_independent_located_chunks(tmp_path):
    doc=ExtractedDocument(source=material(tmp_path).source, parser_version="fixture", processing_key="test", markdown="", page_count=1,
        elements=[DocumentElement(element_id="heading", label="section_header", text="Probe geometry", heading_level=1, provenance=[Provenance(page=1)]),
                  DocumentElement(element_id="pic1", label="picture", text="", image="element-1.png", provenance=[Provenance(page=1,bbox=(0,0,100,100))]),
                  DocumentElement(element_id="pic2", label="picture", text="Figure: transflection tip.", caption="Figure: transflection tip.", image="element-2.png", provenance=[Provenance(page=1)])])
    batch=DocumentChunker(Settings()).process_material(doc)
    images=[item for item in batch.items if item.element_type=="image"]
    assert len(images)==2
    assert images[0].image is not None and images[0].image.filename=="element-1.png"
    assert images[0].image.caption==""
    assert images[0].provenance[0].bbox==(0,0,100,100)
    assert images[1].image is not None and images[1].image.caption=="Figure: transflection tip."
    assert all(item.element_ids in (["pic1"],["pic2"]) and item.parent_context_id for item in images)
    assert all("not been interpreted" in item.text for item in images)


def test_image_only_section_is_not_dropped_and_missing_crop_is_explicit(tmp_path):
    doc=ExtractedDocument(source=material(tmp_path).source, parser_version="fixture", processing_key="test", markdown="", page_count=1,
        elements=[DocumentElement(element_id="pic", label="picture", text="", image="element-1.png", provenance=[Provenance(page=1)])])
    assert DocumentChunker(Settings()).process_material(doc).items[0].element_type=="image"
    doc.elements[0].image=None
    with pytest.raises(ValueError,match="missing its retained crop"):
        DocumentChunker(Settings()).process_material(doc)
