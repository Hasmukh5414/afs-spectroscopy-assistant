import json
import time
from dataclasses import replace

import pytest
from langchain_core.messages import ToolMessage

from afs_assistant.config import Settings
from afs_assistant.core.models import Corpus, EvidenceItem, Locator, SourceVersion
from afs_assistant.retrieval.retriever import Retrieval
from afs_assistant.workflow.agent import AgentResponder
from afs_assistant.workflow.tools import InvestigationTools
from tests.unit.test_investigation import FormattingGateway, InvestigatingModel


SECTION_BODY = " Probe geometry, sample presentation and chemometric calibration determine what the method can report."


def item(evidence_id, text, source="S01", heading=(), element_ids=(), **extra):
    # Include method definitions and unrelated context to detect accidental expansion.
    text = text + SECTION_BODY if heading else text
    return EvidenceItem(evidence_id=evidence_id, source_id=source, source_version="a"*64, title=source, text=text,
        locator=Locator(kind="page", label="PDF p. 1"), kind="application", heading_path=list(heading),
        element_ids=list(element_ids), **extra)


def retrieval(tmp_path):
    sources = [SourceVersion(source_id=s, sha256="a"*64, title=s, filename=f"{s}.pdf", uri=f"urn:afs:{s}",
        kind="application", expected_pages=1, authority="provided") for s in ("S01", "S02", "AFS_TABLE")]
    items = [
        item("overview", "NIR penetrates millimetres; mid-IR needs ATR; Raman is weak; UV-visible overlaps.",
             heading=["3.1. Information Capability of Spectral Methods"]),
        item("nir-section", "NIR spectra (780-2500 nm) contain overtones; diffuse reflection suits powders.",
             heading=["5.2. Near-IR Spectrometry"]),
        item("raman-section", "Raman spectroscopy records weak scattering.", heading=["5.3. Raman Spectroscopy"]),
        item("dairy", "NIR reflection is used for opaque dairy products.", heading=["4. Inline Milk Analysis."]),
        item("fiber-chart", "Slide 6 (transcribed chart): fiber transmission range by wavelength. VIS-NIR silica fibers: 350 nm to 2400 nm.",
             source="S02", element_ids=["curated-S02-p6-fiber-transmission"]),
        *[item(f"row-{n}", f"method: NIR spectroscopy\nmeasurement_technique: {technique}\nsample_type: {sample}", source="AFS_TABLE",
               worksheet="method_comparison", structured_data={"method": "NIR spectroscopy", "measurement_technique": technique,
               "sample_type": sample}) for n, (technique, sample) in enumerate([("cuvette", "liquid"),
               ("diffuse reflection probe", "scattering media, powder"), ("integrating sphere", "solid, powder")])],
    ]
    corpus = Corpus(release="seed-test", items=items, sources=sources)
    settings = Settings(database=tmp_path/"afs.sqlite3", data_dir=tmp_path/"data", run_timeout=90)
    return settings, Retrieval({"corpus": corpus.model_dump_json(), "vectors": "[]", "model": ""}, settings=settings)



def tools_for(tmp_path):
    settings, r = retrieval(tmp_path)
    tools = InvestigationTools(r, settings, None, [], time.monotonic()+90)
    return tools, {tool.name: tool for tool in tools.tools()}


def test_named_methods_do_not_preload_evidence(tmp_path):
    settings, r = retrieval(tmp_path)
    responder = AgentResponder(r, settings, FormattingGateway(), thread_id="explicit",
        model=InvestigatingModel(query="NIR powder"))
    _, sources = responder.answer([{"role": "user", "content": "NIR and Raman over a remote cable?"}])
    assert not any(e["stage"] in ("seeded", "background_added") for e in responder.trace)
    # Code reads only the question's own best matches; further reads are the agent's choice.
    question = next(e for e in responder.trace if e["stage"] == "question_evidence")
    assert question["references"] and question["previews"]
    assert [e["tool"] for e in responder.trace if e["stage"] == "tool"] == ["read_evidence"]
    assert len(sources) == 1
    completed = next(e for e in responder.trace if e["stage"] == "completed")
    # Routing, the one gap turn and the answer: the question read costs no model call.
    assert completed["model_calls"] == 3 and completed["tool_calls"] == 1
    assert set(completed["timings_seconds"]) == {
        "routing", "investigation", "answer_and_validation", "total"}


def test_read_does_not_expand_named_methods(tmp_path):
    tools, api = tools_for(tmp_path)
    result = api["read_evidence"].invoke({"reference": tools.alias("dairy")})
    assert result["evidence_id"] == "dairy"
    assert result["headings"] == ["4. Inline Milk Analysis."]
    assert "method_passages" not in result and "adjacent_evidence" not in result
    assert {v["evidence_id"] for v in tools.inspected.values()} == {"dairy"}


def test_method_lookup_returns_only_requested_comparison_rows(tmp_path):
    tools, api = tools_for(tmp_path)
    result = api["lookup_method_table"].invoke({"method": "NIR"})
    assert {v["source_id"] for v in result["rows"]} == {"AFS_TABLE"}
    assert "method_passages" not in result
    assert all(v["evidence_id"].startswith("row-") for v in tools.inspected.values())


def test_batch_reads_preserve_individual_references_errors_and_restore(tmp_path):
    tools, api = tools_for(tmp_path)
    refs = [tools.alias("dairy"), tools.alias("nir-section")]
    result = api["read_evidence"].invoke({"references": [*refs, refs[0], "E99"]})
    assert [v["evidence_id"] for v in result["passages"]] == ["dairy", "nir-section"]
    assert result["errors"][0]["reference"] == "E99"
    assert tools.calls == 1 and set(tools.inspected) == set(refs)
    restored = InvestigationTools(tools.retrieval, tools.settings, None, [], time.monotonic()+90)
    restored.restore([ToolMessage(content=json.dumps(result), tool_call_id="batch")])
    assert set(restored.inspected) == set(refs)


@pytest.mark.parametrize("arguments", [
    {"references": []}, {"references": ["E1"]*7},
    {"reference": "E1", "references": ["E1"]}, {},
])
def test_invalid_batch_does_not_inspect_evidence(tmp_path, arguments):
    tools, api = tools_for(tmp_path)
    tools.alias("dairy")
    assert "error" in api["read_evidence"].invoke(arguments)
    assert not tools.inspected


def test_batch_shares_evidence_budget(tmp_path):
    tools, _ = tools_for(tmp_path)
    first = tools.expose("dairy", inspected=True)
    limit = len(json.dumps(tools.inspected, ensure_ascii=False).encode()) + 5
    bounded = InvestigationTools(tools.retrieval, replace(tools.settings, evidence_byte_budget=limit),
        None, [], time.monotonic()+90)
    refs = [bounded.alias("dairy"), bounded.alias("nir-section")]
    read = next(t for t in bounded.tools() if t.name == "read_evidence")
    result = read.invoke({"references": refs})
    assert len(result["passages"]) == 1 and result["passages"][0]["text"] == first["text"]
    assert "budget" in result["errors"][0]["error"]
    assert set(bounded.inspected) == {refs[0]} and bounded.calls == 1


def test_outline_entries_can_be_read_in_one_batch(tmp_path):
    tools, api = tools_for(tmp_path)
    outline = api["get_source_outline"].invoke({"source_id": "S01"})
    refs = [entry["reference"] for entry in outline["entries"]][:2]
    result = api["read_evidence"].invoke({"references": refs})
    assert len(result["passages"]) == 2 and set(tools.inspected) == set(refs)
    assert tools.calls == 2


def test_question_read_prefers_distinct_pages_and_caps_rows(tmp_path):
    tools, _ = tools_for(tmp_path)
    read, previews = tools.read_question_evidence("NIR diffuse reflection powders fiber transmission silica")
    passages = [r for r in read if not r.get("table_row")]
    assert len({(r["source_id"], r["location"]) for r in passages}) == len(passages)
    assert sum(bool(r.get("table_row")) for r in read) <= tools.QUESTION_ROWS
    assert [r["reference"] for r in read] == [f"E{i}" for i in range(1, len(read) + 1)]
    assert previews and all(not p["inspected"] for p in previews)


def test_returned_evidence_id_is_readable_but_undiscovered_id_is_rejected(tmp_path):
    tools, api = tools_for(tmp_path)
    tools.alias("dairy")
    found = api["read_evidence"].invoke({"reference": "dairy"})
    assert found["evidence_id"] == "dairy" and found["reference"] == "E1"
    rejected = api["read_evidence"].invoke({"reference": "nir-section"})
    assert "error" in rejected
    assert {v["evidence_id"] for v in tools.inspected.values()} == {"dairy"}


def test_method_table_returns_compact_rows_and_requested_method_descriptions(tmp_path):
    def s01(eid, text, heading, source="S01", **extra):
        return EvidenceItem(evidence_id=eid, source_id=source, source_version="a"*64, title=source, text=text,
            locator=Locator(kind="page", label="PDF p. 10"), kind="application", heading_path=[heading], **extra)
    filler = " Probe geometry, sample presentation and chemometric calibration determine what the method can report in practice."
    items = [
        s01("nir-section", "NIR spectra (780-2500 nm) contain overtones." + filler, "5.2. Near-IR Spectrometry"),
        s01("raman-section", "Raman signals are weak and fluorescence can overlay them." + filler, "5.3. Raman Spectroscopy"),
        s01("overview", "NIR, mid-IR and Raman are vibrational methods." + filler, "3.1. Information Capability"),
        s01("nir-focus", "NIR spectroscopy penetrates several millimetres; NIR suits remote probes." + filler, "3.1. Information Capability"),
        s01("row", "method: NIR", "", source="AFS_TABLE", worksheet="method_comparison",
            structured_data={"method": "NIR spectroscopy", "measurement_technique": "diffuse reflection probe"},
            qualifications=["Ratings are qualitative."]),
    ]
    sources = [SourceVersion(source_id=s, sha256="a"*64, title=s, filename=s, uri=f"urn:{s}", kind="application",
        expected_pages=1, authority="provided") for s in ("S01", "AFS_TABLE")]
    settings = Settings(database=tmp_path/"db.sqlite3", data_dir=tmp_path/"data")
    retrieval = Retrieval({"corpus": Corpus(release="t", items=items, sources=sources).model_dump_json(),
                           "vectors": "[]", "model": ""}, settings=settings)
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic() + 90)
    lookup = next(t for t in tools.tools() if t.name == "lookup_method_table")
    plain = lookup.invoke({"methods": ["NIR"]})
    assert [r["evidence_id"] for r in plain["rows"]] == ["row"] and "method_passages" not in plain
    assert plain["table_notes"] == ["Ratings are qualitative."] and "notes" not in plain["rows"][0]
    family = "NIR spectroscopy"
    ids = [item.evidence_id for item in tools.method_section(family) + tools.capability_paragraphs(family)]
    # Its own section first, then the capability paragraph focused on NIR before the generic overview.
    assert ids[0] == "nir-section" and ids.index("nir-focus") < ids.index("overview")
    assert "raman-section" not in ids
