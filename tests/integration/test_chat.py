import base64
from contextlib import contextmanager
import json
from unittest.mock import patch

from fastapi.testclient import TestClient
import httpx
import pytest

from afs_assistant.api import create_app
from afs_assistant.config import Settings
from afs_assistant.core.models import Corpus, EvidenceImage, EvidenceItem, ImageDescription, Locator, Provenance, SourceVersion
from afs_assistant.core.storage import Conflict
from tests.unit.test_investigation import InvestigatingModel, assessment_reply as nir_assessment_reply


def assessment_reply(text, **kwargs):
    # This module's fixture passage is about ATR; candidates must cite a passage about their method.
    return nir_assessment_reply(text, **kwargs).replace("NIR reflection", "ATR").replace('"NIR"', '"ATR probe"')


def reply(text="The document describes ATR spectroscopy for ethanol in water.", reference="E1"):
    return json.dumps({"parts": [{"kind": "answer", "text": text, "evidence_ids": [reference]}]})


@contextmanager
def services(answer=None, route="technical", selected=None, query=None, intent="qa"):
    """Stub provider transport, exercising actual routing, selection and mapping."""
    answers = iter(answer) if isinstance(answer, list) else None
    def respond(messages, schema, timeout, **kwargs):
        if "methods" in schema["properties"]:
            return json.dumps({"methods": []})
        if "route" in schema["properties"]:
            latest = json.loads(messages[-1]["content"])["latest_user_message"]
            return json.dumps({"route": route, "search_query": query or latest, "wants_experiment": "experiment" in latest.lower(), "intent": intent})
        result = next(answers) if answers is not None else answer or reply()
        if isinstance(result, Exception):
            raise result
        return result
    with patch("afs_assistant.core.gateway.Gateway.embed", return_value=[[1, 0]]) as embed, patch(
        "afs_assistant.core.gateway.Gateway.answer_json", side_effect=respond) as model, patch(
        "afs_assistant.workflow.agent.ChatOpenAI", return_value=InvestigatingModel(query="ATR ethanol", inspect_candidates=selected != [])):
        yield model, embed


@pytest.fixture
def setup(tmp_path):
    settings = Settings(database=tmp_path / "chat.sqlite3", data_dir=tmp_path / "data", mode="openai", embedding_model="test", embedding_dimension=2, background_answers=False)
    source = SourceVersion(source_id="S01", sha256="a" * 64, title="AN007 alcohol measurements", uri="https://example.org/AN007.pdf", filename="AN007.pdf", kind="application", authority="provided", expected_pages=2, artifact_key="a" * 64 + "/parsed")
    items = [EvidenceItem(evidence_id=eid, source_id=source.source_id, source_version=source.sha256, title=source.title,
        text="ATR spectroscopy of ethanol in water using a fiber probe.", locator=Locator(kind="page", label="2"),
        provenance=[Provenance(page=2)], kind="application", synthetic=False) for eid in ("a", "b")]
    corpus = Corpus(release="test-v1", items=items, sources=[source])
    app = create_app(settings)
    app.state.store.publish(corpus.release, "test", corpus.model_dump_json(), "test", [[1, 0], [1, 0]])
    page = settings.data_dir / "processed" / source.artifact_key / "page-2.png"
    page.parent.mkdir(parents=True)
    page.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jG1sAAAAASUVORK5CYII="))
    return settings, app


def test_direct_qa_followup_sources_and_restart(setup):
    settings, app = setup
    with TestClient(app) as client, services() as (model, _):
        chat = client.post("/api/chats", json={"text": "Compare ATR for ethanol in water", "request_id": "first"}).json()
        turn = chat["turns"][0]
        assert turn["status"] == "answered"
        assert "(S1)" not in turn["answer"]["parts"][0]["text"]
        assert ",." not in turn["answer"]["parts"][0]["text"]
        path = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/sources/a'
        source = client.get(path).json()
        assert source["original_url"] == "https://example.org/AN007.pdf#page=2"
        assert client.get(source["page_url"]).headers["content-type"] == "image/png"
        assert client.get(path.replace("/sources/a", "/sources/b")).status_code == 404
        followup = client.post(f'/api/chats/{chat["id"]}/messages', json={"text": "What if the temperature is 80°C?", "request_id": "second", "expected_revision": chat["revision"]}).json()
        assert len(followup["turns"]) == 2
        assert "Compare ATR for ethanol in water" in json.dumps(model.call_args.args[0])
        with TestClient(create_app(settings)) as restarted:
            restarted.cookies.update(client.cookies)
            assert restarted.get(f'/api/chats/{chat["id"]}').json()["turns"] == followup["turns"]


def test_idempotency_stale_updates_and_source_ownership(setup):
    _, app = setup
    with TestClient(app) as client, services() as (model, _):
        body = {"text": "Compare ATR and NIR for ethanol", "request_id": "first"}
        chat = client.post("/api/chats", json=body).json()
        count = model.call_count
        repeated = client.post("/api/chats", json=body).json()
        assert repeated["id"] == chat["id"] and len(repeated["turns"]) == 1 and model.call_count == count
        assert client.post(f'/api/chats/{chat["id"]}/messages', json={"text": "Follow up", "request_id": "second", "expected_revision": 1}).status_code == 409
        with TestClient(app) as other:
            assert other.get(f'/api/chats/{chat["id"]}').status_code == 404
            turn = chat["turns"][0]
            assert other.get(f'/api/chats/{chat["id"]}/turns/{turn["id"]}/sources/a/page').status_code == 404


def test_unrelated_question_stops_before_embedding_and_retrieval(setup):
    _, app = setup
    with TestClient(app) as client, services(route="out_of_scope") as (model, embed):
        chat = client.post("/api/chats", json={"text": "Who won a football match?", "request_id": "unrelated"}).json()
        turn = chat["turns"][0]
        assert turn["status"] == "answered"
        assert len(turn["answer"]["parts"]) == 1 and "outside my scope" in turn["answer"]["parts"][0]["text"]
        assert turn["sources"] == {} and not embed.called and model.call_count == 1
        assert not any(t.get("tool") == "search_evidence" for t in turn["trace"])


def test_read_keyword_match_that_is_not_cited_returns_no_sources(setup):
    _, app = setup
    # The question's keyword match is read in code; relevance is decided when answering.
    gap = json.dumps({"parts": [{"kind": "gap", "text": "The supplied material does not document this measurement.", "evidence_ids": []}]})
    with TestClient(app) as client, services(answer=gap, selected=[]):
        chat = client.post("/api/chats", json={"text": "Does the probe support an undocumented measurement?", "request_id": "no-evidence"}).json()
        turn = chat["turns"][0]
        assert turn["status"] == "answered" and turn["sources"] == {}
        assert [p["kind"] for p in turn["answer"]["parts"]] == ["gap"]
        assert turn["answer"]["citation_status"] == "none"


def test_unrequested_proposals_are_removed_and_declared_gap_sources_are_preserved(setup):
    _, app = setup
    payload = json.loads(reply())
    payload["parts"] += [{"kind": "proposal", "text": "Run an unsolicited experiment.", "evidence_ids": ["E1"]},
                         {"kind": "gap", "text": "The supplied passage does not establish this condition.", "evidence_ids": ["E1"]}]
    with TestClient(app) as client, services(answer=json.dumps(payload)):
        turn = client.post("/api/chats", json={"text": "What is ATR spectroscopy?", "request_id": "narrow"}).json()["turns"][0]
        assert all(p["kind"] != "proposal" for p in turn["answer"]["parts"])
        assert turn["answer"]["parts"][-1]["evidence_ids"] == ["a"]
        assert "withheld" not in json.dumps(turn["answer"])


def test_requested_experiment_can_cite_its_inspected_rationale(setup):
    _, app = setup
    response = json.dumps({"parts": [{"kind": "proposal", "text": "The source describes ATR with a fiber probe; test that interface against independent reference measurements.", "evidence_ids": ["E1"]}]})
    with TestClient(app) as client, services(answer=response):
        turn = client.post("/api/chats", json={"text": "Suggest an experiment for ethanol measurement", "request_id": "experiment"}).json()["turns"][0]
        assert turn["answer"]["parts"][0]["kind"] == "proposal" and set(turn["sources"]) == {"a"}


def test_invalid_source_repair_keeps_only_supported_parts_without_notice(setup):
    _, app = setup
    bad = json.loads(reply())
    bad["parts"].append({"kind": "answer", "text": "Unsupported claim.", "evidence_ids": ["invented"]})
    with TestClient(app) as client, services(answer=[json.dumps(bad), reply()]):
        turn = client.post("/api/chats", json={"text": "ATR ethanol water", "request_id": "bad-reference"}).json()["turns"][0]
        assert turn["status"] == "answered" and len(turn["answer"]["parts"]) == 1
        assert list(turn["sources"]) == ["a"] and "withheld" not in json.dumps(turn["answer"])


def test_reported_numbers_are_not_blanket_rejected(setup):
    _, app = setup
    corpus = json.loads(app.state.store.corpus()["corpus"])
    corpus["release"] = "reported-numbers"
    for item in corpus["items"]:
        item["text"] += " Measurements were performed at 25 °C."
    app.state.store.publish("reported-numbers", "test", json.dumps(corpus), "test", [[1, 0], [1, 0]])
    with TestClient(app) as client, services(answer=reply("The study reports measurements at 25 °C; that is a study condition, not a rating for your process.")):
        turn = client.post("/api/chats", json={"text": "What does the study report?", "request_id": "reported"}).json()["turns"][0]
        assert turn["status"] == "answered" and len(turn["answer"]["parts"]) == 1
        assert not any(t["stage"] == "answer_repair" for t in turn["trace"] if "stage" in t)


@pytest.mark.parametrize("failure,code", [
    (TimeoutError("private-service-details"), "timeout"),
    (httpx.ConnectError("private-service-details"), "connection"),
    (httpx.HTTPStatusError("private-service-details", request=httpx.Request("POST", "https://example.org"), response=httpx.Response(503)), "provider_rejected"),
])
def test_provider_failure_and_retry_keep_question_and_hide_payload(setup, failure, code):
    _, app = setup
    with TestClient(app) as client:
        with patch("afs_assistant.core.gateway.Gateway.answer_json", side_effect=failure):
            response = client.post("/api/chats", json={"text": "Compare ATR and NIR", "request_id": "failure"})
        chat = response.json()
        assert "private-service-details" not in response.text and chat["turns"][0]["trace"][-1]["code"] == code
        with services():
            retried = client.post(f'/api/chats/{chat["id"]}/turns/{chat["turns"][0]["id"]}/retry', json={"expected_revision": chat["revision"]}).json()
        assert len(retried["turns"]) == 1 and retried["turns"][0]["status"] == "answered"


def test_malformed_routing_response_fails_closed(setup):
    _, app = setup
    with TestClient(app) as client, patch("afs_assistant.core.gateway.Gateway.answer_json", return_value="invalid JSON"), patch("afs_assistant.core.gateway.Gateway.embed") as embed:
        turn = client.post("/api/chats", json={"text": "ATR ethanol", "request_id": "invalid"}).json()["turns"][0]
        assert turn["status"] == "failed" and turn["trace"][-1]["code"] == "answer_validation" and not embed.called


def test_pending_turn_conflicts_and_recovery(setup):
    _, app = setup
    store = app.state.store
    case = store.create_case("owner", "Initial question")
    turn, _ = store.start_chat_turn(case["id"], "owner", 1, "Initial question", "first", "test-v1")
    with pytest.raises(Conflict):
        store.start_chat_turn(case["id"], "owner", 2, "Follow up", "second", "test-v1")
    store.recover_interrupted()
    assert store.get_chat(case["id"], "owner")["turns"][0]["status"] == "failed"


def test_exports_preserve_release_sources_and_owner_scope(setup):
    _, app = setup
    with TestClient(app) as client, services():
        chat = client.post("/api/chats", json={"text": "ATR ethanol", "request_id": "export"}).json()
        turn = chat["turns"][0]
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export'
        result = client.get(url).json()
        assert result["release"] == "test-v1" and result["sources"] == turn["sources"]
        assert client.get(url+"?format=md").headers["content-type"].startswith("text/markdown")
        assert client.get(url+"?format=html").status_code == 422
        with TestClient(app) as other:
            assert other.get(url).status_code == 404


def test_retired_release_answers_do_not_enter_new_source_context(setup):
    _, app = setup
    with TestClient(app) as client, services(answer=reply("RETIRED_SOURCE_ONLY_CONTENT")):
        chat = client.post("/api/chats", json={"text": "ATR ethanol", "request_id": "old"}).json()
    snapshot = app.state.store.corpus()
    corpus = Corpus.model_validate_json(snapshot["corpus"])
    corpus.release = "new-official"
    app.state.store.publish(corpus.release, "new", corpus.model_dump_json(), "test", [[1, 0], [1, 0]])
    with TestClient(app) as following, services() as (model, _):
        following.cookies.update(client.cookies)
        answer = following.post(f'/api/chats/{chat["id"]}/messages',
            json={"text": "What should we check next?", "request_id": "next",
                "expected_revision": chat["revision"]}).json()
        assert answer["turns"][-1]["status"] == "answered"
        assert "RETIRED_SOURCE_ONLY_CONTENT" not in json.dumps(model.call_args.args[0])


def test_retained_original_hash_and_owner_are_checked(setup):
    import hashlib
    settings, app = setup
    corpus = Corpus.model_validate_json(app.state.store.corpus()["corpus"])
    corpus.release = "retained-original"
    content = b"%PDF-1.4\n% retained original fixture\n"
    source = corpus.sources[0]
    source.sha256 = hashlib.sha256(content).hexdigest()
    source.retained_path = f"originals/{source.sha256}/S01.pdf"
    original = settings.data_dir/source.retained_path
    original.parent.mkdir(parents=True)
    original.write_bytes(content)
    for item in corpus.items:
        item.source_version = source.sha256
    app.state.store.publish(corpus.release, "retained", corpus.model_dump_json(), "test", [[1, 0], [1, 0]])
    with TestClient(app) as client, services():
        chat = client.post("/api/chats", json={"text": "ATR ethanol", "request_id": "original"}).json()
        turn = chat["turns"][0]
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/sources/a'
        assert client.get(url).json()["original_url"].endswith("/original#page=2")
        assert client.get(url+"/original").content == content
        with TestClient(app) as other:
            assert other.get(url+"/original").status_code == 404
        original.write_bytes(b"changed")
        assert client.get(url+"/original").status_code == 404


def test_background_answer_reports_progress_without_duplicate_work(setup):
    from dataclasses import replace
    from threading import Event
    import time
    from afs_assistant.core.models import AnswerPart, ChatAnswer
    settings, old_app = setup
    app = create_app(replace(settings, background_answers=True))
    entered, release = Event(), Event()
    def respond(responder, history):
        responder.trace.append({"stage": "investigating", "model_call": 1})
        responder.progress(responder.trace)
        entered.set()
        assert release.wait(3)
        return ChatAnswer(parts=[AnswerPart(kind="gap", text="More information is needed.")]), {}
    with TestClient(app) as client, patch("afs_assistant.workflow.chat.ChatResponder.answer", autospec=True, side_effect=respond) as answer:
        try:
            body = {"text": "Assess this process.", "request_id": "background"}
            chat = client.post("/api/chats", json=body).json()
            assert entered.wait(1)
            pending = client.get(f'/api/chats/{chat["id"]}').json()
            assert pending["turns"][0]["status"] == "pending"
            assert pending["turns"][0]["trace"][-1]["stage"] == "investigating"
            assert client.post("/api/chats", json=body).json()["id"] == chat["id"]
            assert answer.call_count == 1
        finally:
            release.set()
        for _ in range(100):
            done = client.get(f'/api/chats/{chat["id"]}').json()
            if done["turns"][0]["status"] != "pending":
                break
            time.sleep(.01)
        assert done["turns"][0]["status"] == "answered"


@pytest.mark.parametrize("with_description", [False, True])
def test_named_figure_and_crops_remain_owner_scoped(setup, with_description):
    settings, app = setup
    corpus = Corpus.model_validate_json(app.state.store.corpus()["corpus"])
    corpus.release = "image-v2"
    corpus.items[0].element_type = "image"
    corpus.items[0].image = EvidenceImage(filename="element-1.png", caption="Figure 2. Probe diagram")
    if with_description:
        corpus.items[0].image.description = ImageDescription(text="A probe diagram with a fiber connected to its sampling tip.", model="vision", prompt_sha256="a" * 64, image_sha256="b" * 64, cache_key="c" * 64, generated_at="2026-10-16")
    folder = settings.data_dir / "processed" / corpus.sources[0].artifact_key
    (folder / "element-1.png").write_bytes((folder / "page-2.png").read_bytes())
    app.state.store.publish(corpus.release, "image", corpus.model_dump_json(), "test", [[1, 0], [1, 0]])
    with TestClient(app) as client, patch("afs_assistant.core.gateway.Gateway.answer_json", side_effect=AssertionError("Named figure display must not call a model")):
        chat = client.post("/api/chats", json={"text": "Show Figure 2 from AN007", "request_id": "figure"}).json()
        turn = chat["turns"][0]
        detail = client.get(f'/api/chats/{chat["id"]}/turns/{turn["id"]}/sources/a').json()
        assert turn["status"] == "answered" and client.get(detail["image_url"]).status_code == 200
        with TestClient(app) as other:
            assert other.get(detail["image_url"]).status_code == 404


def test_ui_has_no_forced_experiment_or_internal_task_notes(setup):
    _, app = setup
    with TestClient(app) as client:
        assert 'id="composer"' in client.get("/").text
        script = client.get("/assets/app.js").text
        for removed in ("Suggested first experiment", "Known source issues:", 'make("h3","Source notes")'):
            assert removed not in script


@pytest.mark.parametrize("failure", ["validation", "timeout"])
def test_failed_assessment_never_publishes_draft_or_sources(setup, failure):
    _, app = setup
    invalid = "not a structured answer"
    responses = [TimeoutError("Formatting timed out")] if failure == "timeout" else [invalid, invalid]
    with TestClient(app) as client, services(answer=responses, intent="assessment"):
        chat = client.post("/api/chats", json={"text": "Assess powder.", "request_id": "review-failure"}).json()
        turn = chat["turns"][-1]
        assert turn["status"] == "failed" and turn["answer"] is None and turn["sources"] == {}
        assert turn["trace"][-1]["code"] == ("timeout" if failure == "timeout" else "answer_validation")
        assert not any(e.get("stage") == "completed" for e in turn["trace"])
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export'
        assert client.get(url).status_code == 404
        assert client.get(url + "?format=md").status_code == 404


def test_export_citation_label_does_not_claim_scientific_support(setup):
    _, app = setup
    with TestClient(app) as client, services():
        chat = client.post("/api/chats", json={"text": "What is ATR?", "request_id": "citation-label"}).json()
        turn = chat["turns"][-1]
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export'
        payload = client.get(url).json()["answer"]
        assert payload["citation_status"] == "present" and "evidence_coverage" not in payload
        markdown = client.get(url + "?format=md").text
        assert "Source citations: present (not scientific verification)" in markdown


@pytest.mark.parametrize("repair", ["repeated_claim", "invalid_json", "timeout"])
def test_failed_quantity_repair_cannot_be_delivered_or_exported(setup, repair):
    _, app = setup
    invalid = assessment_reply("The probe withstands 180 °C.")
    revised = {"repeated_claim": invalid, "invalid_json": "not json",
               "timeout": TimeoutError("Repair timed out")}[repair]
    with TestClient(app) as client, services(answer=[invalid, revised], intent="assessment") as (model, _):
        chat = client.post("/api/chats", json={"text": "Assess powder.", "request_id": "quantity-failure"}).json()
        turn = chat["turns"][-1]
        assert turn["status"] == "failed" and turn["answer"] is None and turn["sources"] == {}
        assert model.call_count == 4  # Routing, method shortlist, generation and exactly one repair.
        assert not any(event.get("stage") == "completed" for event in turn["trace"])
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export'
        assert client.get(url).status_code == 404
        assert client.get(url + "?format=md").status_code == 404


@pytest.mark.parametrize("field", ["spectral_range", "measurement_concept"])
def test_valid_response_and_export_survive_an_unsupported_optional_detail(setup, field):
    _, app = setup
    payload = json.loads(assessment_reply("The source describes a measurement approach worth testing."))
    payload["assessment"]["candidates"][0][field] = "Unsupported exact setting 180 nm."
    raw = json.dumps(payload)
    with TestClient(app) as client, services(answer=[raw, raw], intent="assessment"):
        chat = client.post("/api/chats", json={"text": "Assess powder.", "request_id": "optional-detail"}).json()
        turn = chat["turns"][-1]
        assert turn["status"] == "answered" and turn["sources"]
        assert "180 nm" not in json.dumps(turn["answer"])
        assert turn["answer"]["assessment"]["candidates"][0][field] == ""
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export'
        assert client.get(url).status_code == 200
        assert "180 nm" not in client.get(url + "?format=md").text


def test_assessment_report_contains_full_readable_case_not_raw_json(setup):
    _, app = setup
    payload = json.loads(assessment_reply("An approach is worth testing."))
    candidate = payload["assessment"]["candidates"][0]
    candidate["measurement_concept"] = "Use an appropriate reflection probe at the process interface."
    candidate["limitations"] = ["Applicability to this material needs validation."]
    payload["assessment"]["questions"] = [{"field": "requirements",
        "question": "What prediction error is acceptable?", "affects": "Sets the study acceptance criteria."}]
    payload["assessment"]["experiment"]["steps"] = ["Acquire representative samples.",
        "Compare measurements with an independent reference.", "Validate on separate samples."]
    with TestClient(app) as client, services(answer=json.dumps(payload), intent="assessment"):
        chat = client.post("/api/chats", json={"text": "Assess powder.", "request_id": "readable-report"}).json()
        turn = chat["turns"][-1]
        assert turn["status"] == "answered"
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export'
        report = client.get(url + "?format=md").text
        for value in ("## Process requirements and constraints", "## Approaches worth testing",
                      "Measurement / probe concept", "What prediction error is acceptable?",
                      "**Limitations:**", "Applicability to this material needs validation.", "## First feasibility study",
                      "1. Acquire representative samples.", "3. Validate on separate samples.",
                      "## Criteria to agree and check", "## Sources"):
            assert value in report
        assert "Decision status: Provisional feasibility assessment" in report
        assert "ready_for_review" not in report
        assert "~~~json" not in report and '"candidates":' not in report
        assert client.get(url).json()["answer"]["assessment"]["candidates"][0]["measurement_concept"] == candidate["measurement_concept"]


def test_field_citations_are_resolved_in_json_and_readable_report(setup):
    _, app = setup
    payload = json.loads(assessment_reply("An approach is worth testing."))
    payload["assessment"]["candidates"][0]["limitations"] = ["The inspected passage does not establish process suitability. [E1]"]
    payload["assessment"]["experiment"]["steps"] = ["Test representative samples. [E1]"]
    with TestClient(app) as client, services(answer=json.dumps(payload), intent="assessment"):
        chat = client.post("/api/chats", json={"text": "Assess powder.", "request_id": "field-sources"}).json()
        turn = chat["turns"][0]
        assert turn["status"] == "answered"
        citations = turn["answer"]["citations"]
        assert {c["field"] for c in citations} == {
            "assessment.candidates[0].limitations[0]", "assessment.experiment.steps[0]"}
        assert all(c["evidence_ids"] == ["a"] for c in citations)
        assert "[E1]" not in json.dumps(turn["answer"])
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/export?format=md'
        report = client.get(url).text
        assert "Test representative samples. [1]" in report
        assert "process suitability. [1]" in report


@pytest.mark.parametrize("match", ["exact", "different", "ambiguous", "other_version"])
def test_caption_preview_requires_unique_same_version_caption(setup, match):
    settings, app = setup
    corpus = Corpus.model_validate_json(app.state.store.corpus()["corpus"])
    corpus.release = "caption-v2"
    caption = "Fig. 2. A combined fiber probe."
    corpus.items[0].text = "Fig.\n2.\nA combined fiber probe."
    image = corpus.items[1]
    image.text = "A diagram of a combined fiber probe."
    image.element_type = "image"
    image.image = EvidenceImage(filename="element-1.png",
        caption=caption if match != "different" else "Fig. 3. Another probe.")
    if match == "other_version":
        image.source_version = "b" * 64
    if match == "ambiguous":
        duplicate = image.model_copy(deep=True)
        duplicate.evidence_id = "duplicate"
        corpus.items.append(duplicate)
    folder = settings.data_dir / "processed" / corpus.sources[0].artifact_key
    (folder / "element-1.png").write_bytes((folder / "page-2.png").read_bytes())
    app.state.store.publish(corpus.release, "caption-test", corpus.model_dump_json(),
        "test", [[1, 0]] * len(corpus.items))
    with TestClient(app) as client, services(reply("The caption describes a combined fiber probe.")):
        chat = client.post("/api/chats", json={"text": "Explain the probe.", "request_id": "caption"}).json()
        turn = chat["turns"][0]
        assert turn["status"] == "answered"
        url = f'/api/chats/{chat["id"]}/turns/{turn["id"]}/sources/a'
        detail = client.get(url).json()
        assert detail["evidence"]["text"] == corpus.items[0].text
        assert detail["evidence"]["image"] is None
        if match == "exact":
            assert detail["evidence"]["display_figure"]["evidence_id"] == "b"
            assert client.get(detail["image_url"]).status_code == 200
            with TestClient(app) as other:
                assert other.get(detail["image_url"]).status_code == 404
        else:
            assert detail["image_url"] is None
            assert "display_figure" not in detail["evidence"]
            assert client.get(url + "/image").status_code == 404
        # Display association does not rewrite the saved evidence or add a citation.
        stored = client.get(f'/api/chats/{chat["id"]}').json()["turns"][0]
        assert list(stored["sources"]) == ["a"]
        assert "display_figure" not in stored["sources"]["a"]
