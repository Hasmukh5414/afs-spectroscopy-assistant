"""Opt-in actual parser test; no fake conversion and no model-service request."""
import hashlib
import os
from pathlib import Path

import pytest

from afs_assistant.config import Settings
from afs_assistant.core.models import SourceMaterial, SourceVersion
from afs_assistant.indexing.chunking import DocumentChunker
from afs_assistant.indexing.extractor import DoclingExtractor


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("AFS_TEST_PDF"), reason="Set AFS_TEST_PDF to an actual retained PDF")
def test_real_pdf_retains_pages_tables_and_provenance(tmp_path):
    from pypdf import PdfReader
    path = Path(os.environ["AFS_TEST_PDF"]).resolve()
    source = SourceVersion(source_id="parser-check", sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        title="Real PDF parser verification", uri="local-test-input", filename=path.name, kind="application",
        expected_pages=len(PdfReader(path).pages))
    settings = Settings()
    document = DoclingExtractor(settings).extract(SourceMaterial(source=source, path=path), tmp_path)
    batch = DocumentChunker(settings).process_material(document)
    assert document.page_count == source.expected_pages
    assert len(document.page_images) == source.expected_pages
    assert (tmp_path / "docling.json").is_file() and (tmp_path / "document.md").is_file()
    tables = [element for element in document.elements if element.label == "table"]
    assert tables and all(element.table_data.get("table_cells") for element in tables)
    assert any(item.element_type == "table_row" for item in batch.items)
    assert batch.items and all(item.provenance for item in batch.items)
    assert all(1 <= location.page <= source.expected_pages for item in batch.items for location in item.provenance)
    assert all(not item.synthetic and item.review_status == "extracted" for item in batch.items)
