import json
import time
from unittest.mock import Mock

import pytest

from afs_assistant.core.gateway import Gateway
from afs_assistant.core.models import Corpus, EvidenceImage, EvidenceItem, ImageDescription, Locator
from afs_assistant.retrieval.retriever import Retrieval
from afs_assistant.config import Settings
from afs_assistant.workflow.tools import InvestigationTools
from afs_assistant.workflow.query import plan_query


def test_query_planner_retains_original_application_and_latest_correction():
    history = [{"role": "user", "content": "Measure ethanol concentration in water."},
               {"role": "assistant", "content": "An unverified assistant claim."}]
    history += [{"role": "user", "content": f"Follow-up {i}"} for i in range(15)]
    history.append({"role": "user", "content": "Actually use methanol instead, with a cloudy liquid."})
    gateway = Mock(spec=Gateway)
    gateway.answer_json.return_value = json.dumps({"route": "technical", "search_query": "Methanol monitoring in cloudy water", "wants_experiment": False})
    plan = plan_query(history, gateway, 20)
    payload = json.loads(gateway.answer_json.call_args.args[0][-1]["content"])
    supplied = [*payload["earlier_user_messages"], payload["latest_user_message"]]
    assert supplied[0] == "Measure ethanol concentration in water."
    assert supplied[-1] == history[-1]["content"] and len(supplied) == 12
    assert "unverified assistant" not in json.dumps(supplied)
    assert plan.search_query == "Methanol monitoring in cloudy water"


def test_compact_image_context_has_description_once_and_omits_hashes_and_task_ids():
    marker = "An absorbance plot with several overlapping spectral curves."
    item = EvidenceItem(evidence_id="image", source_id="s", source_version="v", title="Study", text=marker,
        locator=Locator(kind="page", label="1"), kind="application", issue_ids=["Q03", "Q05"],
        element_type="image", image=EvidenceImage(filename="element-1.png", caption="Figure 1. Spectra",
            nearby_text="Aqueous calibration study.", description=ImageDescription(text=marker, model="vision",
                prompt_sha256="a" * 64, image_sha256="b" * 64, cache_key="c" * 64, generated_at="2026-10-16")))
    corpus = Corpus(release="test", items=[item])
    retrieval = Retrieval({"corpus": corpus.model_dump_json(), "model": "", "vectors": "[]"})
    tools = InvestigationTools(retrieval, Settings(), None, [], time.monotonic()+90)
    payload = json.dumps(tools.expose(item.evidence_id, inspected=True))
    assert payload.count(marker) == 1
    assert all(value not in payload for value in ("Q03", "Q05", "cache_key", "image_sha256", "element-1.png"))
    assert len(payload.encode()) <= tools.settings.evidence_byte_budget


def test_evidence_budget_caps_many_long_multibyte_passages():
    items = [EvidenceItem(evidence_id=str(i), source_id="s", source_version="v", title="Study",
        text=f"Passage {i}. " + "測定条件とスペクトル " * 250,
        locator=Locator(kind="page", label=str(i + 1)), kind="application") for i in range(20)]
    corpus = Corpus(release="large", items=items)
    retrieval = Retrieval({"corpus": corpus.model_dump_json(), "model": "", "vectors": "[]"})
    tools = InvestigationTools(retrieval, Settings(evidence_byte_budget=12000), None, [], time.monotonic()+90)
    for item in items:
        try:
            tools.expose(item.evidence_id, inspected=True)
        except ValueError:
            break
    assert 0 < len(tools.inspected) < len(items)
    assert len(json.dumps(tools.inspected, ensure_ascii=False).encode()) <= tools.settings.evidence_byte_budget
    assert all(p["truncated"] for p in tools.inspected.values())
