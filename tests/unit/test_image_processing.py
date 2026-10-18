import base64
from dataclasses import replace
import json
from unittest.mock import patch
from threading import Barrier

import pytest

from afs_assistant.config import Settings
from afs_assistant.core.gateway import Gateway, VisionTokenLimitError
from afs_assistant.core.models import DocumentElement, Provenance
from afs_assistant.core.storage import Store
from afs_assistant.indexing.builder import EvidenceBuilder
from afs_assistant.indexing.chunking import DocumentChunker
from afs_assistant.indexing.image_processor import ImageProcessor, validate_description
from afs_assistant.indexing.material_cache import MaterialCache
from tests.unit.test_indexing import FixtureExtractor, FixtureGateway, material, receipt

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jG1sAAAAASUVORK5CYII=")


def test_large_slide_transport_is_bounded_without_changing_original():
    from io import BytesIO
    from PIL import Image
    pixels = Image.effect_noise((1920, 1080), 80).convert("RGB")
    original = BytesIO()
    pixels.save(original, format="PNG")
    content = original.getvalue()
    assert len(content) > 600_000
    url = Gateway.vision_image_url(content)
    assert url.startswith("data:image/jpeg;base64,") and len(url) < 810_000
    assert original.getvalue() == content
    assert Gateway.vision_image_url(PNG).startswith("data:image/png;base64,")


def test_visual_search_retains_labels_but_omits_inferred_peak_intervals():
    from afs_assistant.core.source_policy import visual_search_text
    text = "The axis is Wavenumber [cm⁻¹], ranging from 4000 to 2000. The legend labels 0.1% Water and Pure Oil. The spectra exhibit a prominent sharp peak between 2250 and 2500 cm⁻¹."
    safe, changed = visual_search_text(text)
    assert changed and "2250" not in safe
    assert "4000" in safe and "0.1% Water" in safe
DESCRIPTION = "A diagram with a fiber leading to a probe tip beside a labeled sample container."


class ImageExtractor(FixtureExtractor):
    def extract(self, source_material, directory):
        doc = super().extract(source_material, directory)
        (directory / "element-1.png").write_bytes(PNG)
        caption = "Figure 1: sampling setup for " + source_material.source.source_id + "."
        doc.elements.append(DocumentElement(element_id="picture", label="picture", text=caption,
            caption=caption, image="element-1.png", provenance=[Provenance(page=1, bbox=(0, 0, 1, 1))]))
        return doc


def test_descriptions_are_embedded_and_cached_without_reparsing(tmp_path):
    settings = Settings(data_dir=tmp_path, vision_model="vision-a", embedding_model="embed-a", embedding_dimension=2)
    store = Store(tmp_path / "store.sqlite3")
    extractor = ImageExtractor()
    gateway = FixtureGateway(settings)
    inputs = receipt(tmp_path, [material(tmp_path)])
    builder = EvidenceBuilder(settings, store, gateway, extractor)
    with patch.object(Gateway, "describe_image", return_value=DESCRIPTION) as vision, patch.object(gateway, "embed", wraps=gateway.embed) as embed:
        first = builder.process_materials(inputs, "vision-a", True, True)
        repeated = builder.process_materials(inputs, "vision-a", True, True)
        assert first["images"] == {"total": 1, "completed": 1, "generated": 1, "cached": 0}
        assert repeated["images"]["cached"] == 1
        assert extractor.calls == vision.call_count == embed.call_count == 1
        assert DESCRIPTION in embed.call_args.args[0][-1]
        assert "Caption: Figure 1" in embed.call_args.args[0][-1]
        assert "not been interpreted" not in embed.call_args.args[0][-1]
        changed_embedding = replace(settings, embedding_model="embed-b")
        result = EvidenceBuilder(changed_embedding, store, FixtureGateway(changed_embedding), extractor).process_materials(inputs, "embed-b", True, True)
        assert result["images"]["cached"] == 1 and vision.call_count == 1
        changed_vision = replace(settings, vision_model="vision-b")
        result = EvidenceBuilder(changed_vision, store, FixtureGateway(changed_vision), extractor).process_materials(inputs, "vision-b", True, True)
        assert result["images"]["generated"] == 1 and vision.call_count == 2
        assert extractor.calls == 1
    published = json.loads((tmp_path / "releases" / "vision-a" / "corpus.json").read_text())
    image = published["items"][-1]
    assert image["image"]["description"]["review_status"] == "unreviewed"
    assert image["image"]["interpretation"] == "vision_description_unreviewed"
    assert image["provenance"][0]["bbox"] == [0, 0, 1, 1]
    assert image["review_status"] == "extracted"


def test_image_context_prompt_and_bytes_invalidate_description_cache(tmp_path):
    source = material(tmp_path)
    settings = Settings(data_dir=tmp_path, vision_model="vision-a")
    cache = MaterialCache(tmp_path)
    directory = cache.artifact_directory(source.source.sha256, "fixture-parser-v1")
    document = ImageExtractor().extract(source, directory)
    chunker = DocumentChunker(settings)
    batch = chunker.process_material(document)
    with Gateway(settings) as gateway, patch.object(Gateway, "describe_image", return_value=DESCRIPTION) as vision:
        processor = ImageProcessor(settings, cache, gateway)
        enriched = processor.enrich(batch, document, lambda counts: None, chunker.count)
        assert enriched.items[-1].evidence_id != batch.items[-1].evidence_id
        assert batch.items[-1].image is not None and batch.items[-1].image.description is None
        processor.enrich(batch, document, lambda counts: None, chunker.count)
        assert vision.call_count == 1
        assert batch.items[-1].image is not None
        batch.items[-1].image.nearby_text = "Different caption context."
        processor.enrich(batch, document, lambda counts: None, chunker.count)
        assert vision.call_count == 2
        (directory / "element-1.png").write_bytes(PNG + b"different image bytes")
        processor.enrich(batch, document, lambda counts: None, chunker.count)
        assert vision.call_count == 3
        with patch.dict(processor.profile, {"prompt_sha256": "f" * 64}):
            processor.enrich(batch, document, lambda counts: None, chunker.count)
        assert vision.call_count == 4


def test_failed_vision_run_preserves_active_release_and_resumes_cached_images(tmp_path):
    settings = Settings(data_dir=tmp_path, vision_model="vision-a")
    store = Store(tmp_path / "store.sqlite3")
    gateway = FixtureGateway(settings)
    inputs = receipt(tmp_path, [material(tmp_path, "one"), material(tmp_path, "two")])
    builder = EvidenceBuilder(settings, store, gateway, ImageExtractor())
    builder.process_materials(inputs, "caption-only")
    with patch.object(Gateway, "describe_image", side_effect=[DESCRIPTION, TimeoutError("private provider payload")]):
        failed = builder.process_materials(inputs, "vision", describe_images=True)
    assert failed["status"] == "failed" and "private provider" not in json.dumps(failed)
    active = store.corpus()
    assert active is not None and active["id"] == "caption-only"
    assert store.corpus("vision") is None
    with patch.object(Gateway, "describe_image", return_value=DESCRIPTION) as vision:
        resumed = builder.process_materials(inputs, "vision", describe_images=True)
    assert resumed["status"] == "ready_for_retrieval"
    assert resumed["images"]["cached"] == resumed["images"]["generated"] == vision.call_count == 1


def test_parallel_descriptions_preserve_order_and_share_duplicate_cache_keys(tmp_path):
    settings = Settings(data_dir=tmp_path, vision_model="vision-a", vision_workers=2)
    source = material(tmp_path)
    cache = MaterialCache(tmp_path)
    directory = cache.artifact_directory(source.source.sha256, "fixture-parser-v1")
    document = ImageExtractor().extract(source, directory)
    chunker = DocumentChunker(settings)
    batch = chunker.process_material(document)
    image = batch.items[-1]
    assert image.image is not None
    other = image.model_copy(deep=True)
    other.evidence_id = "other"
    assert other.image is not None
    other.image.caption = "Another diagram."
    duplicate = image.model_copy(deep=True)
    duplicate.evidence_id = "duplicate"
    batch.items = [image, other, duplicate]
    gate = Barrier(2)
    def describe(*args):
        gate.wait(timeout=5)
        return DESCRIPTION
    progress = []
    with Gateway(settings) as gateway, patch.object(Gateway, "describe_image", side_effect=describe) as vision:
        result = ImageProcessor(settings, cache, gateway).enrich(batch, document, progress.append, chunker.count)
    assert vision.call_count == 2
    assert progress[-1] == {"total": 3, "completed": 3, "generated": 2, "cached": 1}
    assert [item.image.caption for item in result.items if item.image] == [image.image.caption, "Another diagram.", image.image.caption]
    assert len({item.evidence_id for item in result.items}) == 3


def test_interrupted_description_run_is_recorded_without_publication(tmp_path):
    settings = Settings(data_dir=tmp_path, vision_model="vision-a")
    store = Store(tmp_path / "store.sqlite3")
    inputs = receipt(tmp_path, [material(tmp_path)])
    builder = EvidenceBuilder(settings, store, FixtureGateway(settings), ImageExtractor())
    with patch.object(Gateway, "describe_image", side_effect=KeyboardInterrupt), pytest.raises(KeyboardInterrupt):
        builder.process_materials(inputs, "interrupted", describe_images=True)
    assert store.corpus() is None
    report = json.loads(next((tmp_path / "runs" / "interrupted").glob("*.json")).read_text())
    assert report["status"] == report["sources"][0]["status"] == "interrupted"


def test_curve_reading_notes_preserve_raw_output_and_cached_description(tmp_path):
    raw = DESCRIPTION + " The spectrum has a peak near 1500 cm-1."
    settings = Settings(data_dir=tmp_path, vision_model="vision-a")
    source = material(tmp_path)
    cache = MaterialCache(tmp_path)
    document = ImageExtractor().extract(source, cache.artifact_directory(source.source.sha256, "fixture-parser-v1"))
    chunker = DocumentChunker(settings)
    batch = chunker.process_material(document)
    with Gateway(settings) as gateway, patch.object(Gateway, "describe_image", return_value=raw) as vision:
        processor = ImageProcessor(settings, cache, gateway)
        flagged = processor.enrich(batch, document, lambda counts: None, chunker.count)
        processor.enrich(batch, document, lambda counts: None, chunker.count)
        assert vision.call_count == 1
    image = flagged.items[-1].image
    assert image is not None and image.description is not None
    assert image.description.text == raw
    assert "peak near 1500" not in flagged.items[-1].text
    assert any("curve-position" in note for note in image.review_notes)
    assert image.review_notes[0] in flagged.items[-1].qualifications


def test_output_limit_retries_once_without_reasoning_and_reuses_that_cache(tmp_path):
    settings = Settings(data_dir=tmp_path, vision_model="vision-a")
    source = material(tmp_path)
    cache = MaterialCache(tmp_path)
    document = ImageExtractor().extract(source, cache.artifact_directory(source.source.sha256, "fixture-parser-v1"))
    chunker = DocumentChunker(settings)
    batch = chunker.process_material(document)
    with Gateway(settings) as gateway, patch.object(Gateway, "describe_image", side_effect=[VisionTokenLimitError(), DESCRIPTION]) as vision:
        processor = ImageProcessor(settings, cache, gateway)
        first = processor.enrich(batch, document, lambda counts: None, chunker.count)
        repeated = processor.enrich(batch, document, lambda counts: None, chunker.count)
        assert vision.call_count == 2
        assert vision.call_args.kwargs["reasoning_effort"] == "none"
    image = first.items[-1].image
    assert image is not None and image.description is not None
    assert image.description.reasoning_effort == "none"
    assert image.description.fallback_from_reasoning == ""
    assert first.items[-1].evidence_id == repeated.items[-1].evidence_id
    assert len(list((tmp_path / "cache/image_descriptions").glob("*.json"))) == 1


def test_repeated_output_limit_never_caches_an_incomplete_description(tmp_path):
    settings = Settings(data_dir=tmp_path, vision_model="vision-a")
    source = material(tmp_path)
    cache = MaterialCache(tmp_path)
    document = ImageExtractor().extract(source, cache.artifact_directory(source.source.sha256, "fixture-parser-v1"))
    chunker = DocumentChunker(settings)
    with Gateway(settings) as gateway, patch.object(Gateway, "describe_image", side_effect=VisionTokenLimitError) as vision:
        processor = ImageProcessor(settings, cache, gateway)
        with pytest.raises(VisionTokenLimitError):
            processor.enrich(chunker.process_material(document), document, lambda counts: None, chunker.count)
        assert vision.call_count == 2
    assert not list((tmp_path / "cache/image_descriptions").glob("*.json"))


@pytest.mark.parametrize("text", ["", "too short", "word word word word, repeated description.",
    "The assistant is unable to see the image supplied in this request."])
def test_empty_repetitive_or_nonvision_output_is_rejected(text):
    with pytest.raises(ValueError):
        validate_description(text)
