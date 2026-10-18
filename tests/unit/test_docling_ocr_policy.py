from afs_assistant.config import Settings
from afs_assistant.indexing.official import OfficialExtractor


def test_official_ocr_runs_inside_docling_with_source_appropriate_modes(tmp_path):
    extractor = OfficialExtractor(Settings(data_dir=tmp_path))
    assert extractor.pdf.settings.do_ocr
    assert extractor.slides.settings.do_ocr
    assert extractor.pdf.ocr_mode == "pdf_aware_layout_regions"
    assert extractor.slides.ocr_mode == "full_page"
    assert not hasattr(extractor, "ocr_image")
    assert not hasattr(extractor, "ocr")
    assert extractor.pdf.processing_key("a"*64) != extractor.slides.processing_key("a"*64)
