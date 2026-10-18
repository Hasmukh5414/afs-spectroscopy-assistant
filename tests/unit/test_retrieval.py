import ast
import math
from pathlib import Path

from afs_assistant.core.lexical_index import build_index, tokens
from afs_assistant.core.models import Corpus, EvidenceContext, EvidenceImage, EvidenceItem, ImageDescription, Locator, SearchRequest
from afs_assistant.config import Settings
from afs_assistant.core.source_policy import decorative_image
from afs_assistant.retrieval.fusion import reciprocal_rank_fusion
from afs_assistant.retrieval.retriever import Retrieval


def test_technical_tokens_and_reused_lexical_index():
    assert tokens("AP12571 C NIR Cu2+ 0.5 mm") == ["ap12571", "c", "nir", "cu2", "0.5", "mm"]
    texts = ("moisture powder", "liquid ethanol", "probe temperature")
    assert build_index(texts) is build_index(texts)
    assert build_index(texts).search("astronomy galaxy", 5) == []
    assert reciprocal_rank_fusion([[0, 1], [1, 0]])[0][1] > 1 / 61
    assert tokens("why is this a logo") == ["logo"]
    assert build_index(("This is a logo for a company.",)).search("why is this", 5) == []


def test_semantic_cutoff_is_inclusive_and_applies_before_candidate_limit():
    items = [EvidenceItem(evidence_id=str(i), source_id="s", source_version="v", title="Spectroscopy",
        text="Measurement passage", locator=Locator(kind="page", label="1"), kind="application") for i in range(3)]
    corpus = Corpus(release="cutoff", items=items)
    class QueryGateway:
        settings = Settings(embedding_model="test")
        def embed(self, texts, timeout):
            return [[1, 0]]
    cutoff = Settings().min_similarity
    vectors = [[cutoff - 0.001, math.sqrt(1 - (cutoff - 0.001) ** 2)], [cutoff, math.sqrt(1 - cutoff ** 2)], [1, 0]]
    import json
    retrieval = Retrieval({"corpus": corpus.model_dump_json(), "vectors": json.dumps(vectors), "model": "test"}, QueryGateway(), Settings())
    result = retrieval.search(SearchRequest(query="unmatchedconcept"))
    assert {hit["evidence"]["evidence_id"] for hit in result["items"]} == {"1", "2"}


def test_only_clearly_decorative_image_descriptions_are_filtered():
    item = EvidenceItem(evidence_id="logo", source_id="s", source_version="v", title="Catalogue", text="A logo",
        locator=Locator(kind="page", label="1"), kind="specification", image=EvidenceImage(filename="element-1.png",
        description=ImageDescription(text="This is a logo for the manufacturer, with colored curved shapes.", model="test", prompt_sha256="a" * 64,
            image_sha256="b" * 64, cache_key="c" * 64, generated_at="2026-10-16")))
    assert decorative_image(item)
    assert item.image is not None and item.image.description is not None
    item.image.description.text = "This plot contains spectra and a small manufacturer logo in one corner."
    assert not decorative_image(item)


def test_parent_context_and_issue_flags_remain_inspectable():
    corpus = Corpus(release="test", contexts=[EvidenceContext(context_id="parent", text="Full table including seal qualifications.")],
        items=[EvidenceItem(evidence_id="row", source_id="catalogue", source_version="v1", title="Probe rating",
            text="AP12571 shaft rating", locator=Locator(kind="page", label="PDF p. 19"), kind="specification",
            parent_context_id="parent", issue_ids=["Q05"], synthetic=False)])
    retriever = Retrieval({"corpus": corpus.model_dump_json(), "model": "", "vectors": "[]"})
    result = retriever.search(SearchRequest(query="AP12571"))
    assert result["items"][0]["evidence"]["issue_ids"] == ["Q05"]
    assert retriever.context("row")["parent_text"] == "Full table including seal qualifications."


def test_production_domain_dependencies_stay_separate():
    root = Path(__file__).parents[2] / "src/afs_assistant"
    for domain, forbidden in [("indexing", "retrieval"), ("retrieval", "indexing")]:
        for source in (root / domain).glob("*.py"):
            imports = [node.module for node in ast.walk(ast.parse(source.read_text())) if isinstance(node, ast.ImportFrom)]
            assert not any(module and module.startswith("afs_assistant." + forbidden) for module in imports), source
