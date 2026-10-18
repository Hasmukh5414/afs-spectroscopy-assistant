import time

import pytest

from afs_assistant.config import Settings
from afs_assistant.core.citations import normalize_citations, object_at
from afs_assistant.core.models import ChatAnswer, Corpus, EvidenceItem, Locator, SearchRequest, SourceVersion
from afs_assistant.retrieval.retriever import Retrieval
from afs_assistant.workflow.tools import InvestigationTools


class EmbeddingGateway:
    """Embeds every query as [1, 0]; passage similarity comes from the stored vectors."""
    def __init__(self, settings):
        self.settings, self.calls = settings, 0

    def embed(self, texts, timeout=None):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


def budget_tools(tmp_path, monkeypatch, gateway=True):
    source = SourceVersion(source_id="S01", sha256="a"*64, title="S01", filename="S01.pdf", uri="urn:afs:S01",
        kind="application", expected_pages=3, authority="provided")
    # Similarity to the query: atr 1.0, milk 0.6, fiber (the NIR section) 0.0.
    texts = {"fiber": "NIR section: silica fibers", "milk": "NIR reflection for milk", "atr": "mid-IR ATR fiber probes"}
    items = [EvidenceItem(evidence_id=eid, source_id="S01", source_version=source.sha256, title="S01", text=text,
        locator=Locator(kind="page", label=f"PDF p. {n}"), kind="application") for n, (eid, text) in enumerate(texts.items(), 1)]
    settings = Settings(database=tmp_path/"afs.sqlite3", data_dir=tmp_path/"data", embedding_model="e")
    snapshot = {"corpus": Corpus(release="budget", items=items, sources=[source]).model_dump_json(),
                "vectors": "[[0, 1], [0.6, 0.8], [1, 0]]", "model": "e"}
    retrieval = Retrieval(snapshot, EmbeddingGateway(settings) if gateway else None, settings)
    monkeypatch.setattr(InvestigationTools, "ANSWER_EVIDENCE", 1)
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    section, row, other = retrieval.items
    tools.method_section = lambda family: [section]
    tools.table_rows = lambda query: [row, other]
    tools.capability_paragraphs = lambda family: []
    tools.add_method_background(["NIR"])
    return tools


def test_answer_budget_keeps_closest_evidence_and_protected_method_section(tmp_path, monkeypatch):
    tools = budget_tools(tmp_path, monkeypatch)
    dropped = tools.focus("Can our silica fiber carry mid-IR?")
    # The NIR section is protected although it is least similar; one embedding call ranks the rest.
    assert {record["evidence_id"] for record in tools.inspected.values()} == {"fiber", "atr"}
    assert [tools.aliases[ref] for ref in dropped] == ["milk"]
    assert tools.retrieval.gateway is not None and tools.retrieval.gateway.calls == 1


def test_answer_budget_changes_nothing_without_embeddings(tmp_path, monkeypatch):
    tools = budget_tools(tmp_path, monkeypatch, gateway=False)
    assert tools.focus("question") == [] and len(tools.inspected) == 3


def test_evidence_within_budget_needs_no_embedding_call(tmp_path, monkeypatch):
    tools = budget_tools(tmp_path, monkeypatch)
    monkeypatch.setattr(InvestigationTools, "ANSWER_EVIDENCE", 3)
    assert tools.focus("question") == []
    assert tools.retrieval.gateway is not None and tools.retrieval.gateway.calls == 0


def test_similarity_rejects_a_different_embedding_model(tmp_path, monkeypatch):
    tools = budget_tools(tmp_path, monkeypatch)
    assert tools.retrieval.gateway is not None
    tools.retrieval.model = "other"
    assert tools.retrieval.similarity("q", tools.retrieval.items) is None
    with pytest.raises(ValueError):
        tools.retrieval.search(SearchRequest(query="q"))


def test_background_never_reads_laboratory_cuvette_rows(tmp_path, monkeypatch):
    tools = budget_tools(tmp_path, monkeypatch)
    section, row, other = tools.retrieval.items
    row.structured_data = {"method": "NIR spectroscopy", "measurement_technique": "cuvette"}
    tools.inspected.clear()
    tools.add_method_background(["NIR"])
    assert {record["evidence_id"] for record in tools.inspected.values()} == {"fiber", "atr"}


def test_field_citation_of_an_objects_reference_slot_merges_into_that_slot():
    answer = ChatAnswer.model_validate({"parts": [{"kind": "answer", "text": "NIR fits.", "evidence_ids": ["E1"]}],
        "citations": [{"field": "parts[0].evidence_ids", "evidence_ids": ["E2"]}]})
    normalized = normalize_citations(answer)
    assert normalized.parts[0].evidence_ids == ["E1", "E2"] and normalized.citations == []


def test_object_at_returns_none_for_a_missing_path():
    assert object_at({"parts": [{"text": "a"}]}, "parts[0]") == {"text": "a"}
    assert object_at({"parts": []}, "parts[3]") is None and object_at({}, "assessment.candidates[0]") is None
