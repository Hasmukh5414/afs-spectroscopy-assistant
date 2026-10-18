import json
import time

import pytest
from pydantic import Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from afs_assistant.config import Settings
from afs_assistant.core.models import Corpus, EvidenceItem, Locator, SourceVersion, ChatAnswer
from afs_assistant.retrieval.retriever import Retrieval
from afs_assistant.workflow.agent import AgentResponder
from afs_assistant.workflow.chat import AnswerValidationError
from afs_assistant.workflow.tools import InvestigationTools


class InvestigatingModel(BaseChatModel):
    """Runs the actual graph; only provider responses are replaced."""
    inspect_candidates: bool = True
    query: str = "NIR powder"
    tool_choices: list[str] = Field(default_factory=list)
    @property
    def _llm_type(self):
        return "test-native-tools"

    def bind_tools(self, tools, **kwargs):
        self.tool_choices.append(kwargs.get("tool_choice", "auto"))
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        observations = [m for m in messages if isinstance(m, ToolMessage)]
        if not observations:
            message = AIMessage(content="", tool_calls=[{
                "name": "search_knowledge", "args": {"query": self.query}, "id": "search-1"}])
        elif (len(observations) == 1 and self.inspect_candidates and isinstance(observations[0].content, str)
                and (result := json.loads(observations[0].content)).get("candidates")):
            message = AIMessage(content="", tool_calls=[{
                "name": "read_evidence", "args": {"reference": result["candidates"][0]["reference"]}, "id": "read-1"}])
        else:
            message = AIMessage(content="NIR reflection is a candidate, supported by E1.")
        return ChatResult(generations=[ChatGeneration(message=message)])


class GapSearchModel(InvestigatingModel):
    """Spends its one gap turn on a targeted search after the code-side question read."""
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        own = [m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id != "question-evidence"]
        if own:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Evidence inspected."))])
        call = {"name": "search_knowledge", "args": {"query": self.query}, "id": "gap-search"}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[call]))])


class FormattingGateway:
    def __init__(self, route="technical", methods=None):
        self.route = route
        self.methods = methods or []
        self.shortlist_requests = []

    def shortlist(self, messages, schema):
        """Answer the assessment method shortlist; None means this was not that call."""
        if "methods" not in schema["properties"]:
            return None
        self.shortlist_requests.append(json.loads(messages[-1]["content"]))
        return json.dumps({"methods": self.methods})

    def answer_json(self, messages, schema, timeout, **kwargs):
        if (shortlist := self.shortlist(messages, schema)) is not None:
            return shortlist
        if "route" in schema["properties"]:
            return json.dumps({"route": self.route, "search_query": "NIR powder" if self.route == "technical" else "",
                "wants_experiment": False, "intent": "qa"})
        return json.dumps({"parts": [{"kind": "answer", "text": "NIR reflection is worth testing.", "evidence_ids": ["E1"]}],
            "intent": "qa", "assessment": None})


def fixture(tmp_path):
    source = SourceVersion(source_id="S01", sha256="a"*64, title="Methods", filename="S01.pdf",
        uri="urn:afs:S01", kind="application", expected_pages=1, authority="provided")
    item = EvidenceItem(evidence_id="nir-powder", source_id="S01", source_version=source.sha256,
        title="Methods", text="NIR diffuse reflection measures powder samples.",
        locator=Locator(kind="page", label="PDF p. 5"), kind="application")
    corpus = Corpus(release="official-test", items=[item], sources=[source])
    settings = Settings(database=tmp_path/"afs.sqlite3", data_dir=tmp_path/"data", run_timeout=90)
    retrieval = Retrieval({"corpus": corpus.model_dump_json(), "vectors": "[]", "model": ""}, settings=settings)
    return settings, retrieval


def test_native_graph_selects_tools_and_restores_checkpoint(tmp_path):
    settings, retrieval = fixture(tmp_path)
    history = [{"role": "user", "content": "Which geometry works for powders?"}]
    model = InvestigatingModel()
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="owner:turn:release",
        model=model)
    answer, sources = responder.answer(history)
    assert answer.parts[0].evidence_ids == ["nir-powder"] and set(sources) == {"nir-powder"}
    # Nothing matched the question's own wording, so the agent's one turn searches;
    # with no later turn to read, that search reads its top passage itself.
    assert next(e for e in responder.trace if e["stage"] == "question_evidence")["references"] == []
    assert [e["tool"] for e in responder.trace if e["stage"] == "tool"] == ["search_knowledge"]
    assert model.tool_choices[:1] == ["required"]
    retry = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="owner:turn:release",
        model=InvestigatingModel())
    retried, sources = retry.answer(history)
    assert retried.parts[0].evidence_ids == ["nir-powder"]
    assert not any(e["stage"] == "tool" for e in retry.trace)


def test_unrelated_request_does_not_invoke_knowledge_tools(tmp_path):
    settings, retrieval = fixture(tmp_path)
    responder = AgentResponder(retrieval, settings, FormattingGateway("out_of_scope"), thread_id="unrelated",
        model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Who won the football match?"}])
    assert "outside my scope" in answer.parts[0].text and not sources
    assert not any(e["stage"] == "tool" for e in responder.trace)


def test_candidates_cannot_be_cited_until_read(tmp_path):
    settings, retrieval = fixture(tmp_path)
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="validation")
    responder.tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    responder.tools.expose("nir-powder")
    answer = ChatAnswer.model_validate({"parts": [{"kind": "answer", "text": "NIR.", "evidence_ids": ["E1"]}]})
    with pytest.raises(ValueError, match="inspected"):
        responder.validate(answer, [{"role": "user", "content": "NIR?"}], "qa")


def test_business_filter_applies_before_search(tmp_path):
    settings, retrieval = fixture(tmp_path)
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    search = tools.tools()[0]
    result = search.invoke({"query": "powder", "roles": ["business"]})
    assert result["candidates"] == []


def test_method_lookup_does_not_match_another_method_in_shared_notes(tmp_path):
    settings, retrieval = fixture(tmp_path)
    item = retrieval.items[0]
    item.worksheet = "method_comparison"
    item.structured_data = {"method": "NIR spectroscopy", "measurement_technique": "diffuse reflection probe"}
    item.text += " Shared notes also mention Raman."
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    lookup = next(tool for tool in tools.tools() if tool.name == "lookup_method_table")
    assert lookup.invoke({"method": "Raman"})["rows"] == []
    assert len(lookup.invoke({"method": "NIR"})["rows"]) == 1
    assert lookup.invoke({"method": "IR"})["rows"] == []


def test_retry_restarts_a_terminal_checkpoint_without_tool_evidence(tmp_path):
    from afs_assistant.workflow.chat import AnswerValidationError
    class NoTools(InvestigatingModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="An ungrounded answer."))])
    settings, retrieval = fixture(tmp_path)
    # No passage matches this wording, so only the agent's tools could ground it.
    history = [{"role": "user", "content": "Which geometry works for powders?"}]
    first = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="restart-no-tools", model=NoTools())
    with pytest.raises(AnswerValidationError, match="did not call"):
        first.answer(history)
    retry = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="restart-no-tools", model=InvestigatingModel())
    answer, sources = retry.answer(history)
    assert answer.parts[0].evidence_ids == ["nir-powder"] and sources


def assessment_reply(text, quote="Assess powder.", gap=False):
    parts = [{"kind": "answer", "text": text, "evidence_ids": ["E1"]}]
    if gap:
        parts.append({"kind": "gap", "text": "Calibration for this powder is not established.", "evidence_ids": []})
    return json.dumps({"intent": "assessment", "parts": parts,
        "assessment": {"status": "ready_for_review", "summary": "Provisional comparison.",
            "candidates": [{"name": "NIR", "claims": [{"text": "NIR reflection is a candidate.", "evidence_ids": ["E1"]}],
                "assumptions": [], "limitations": ["Calibration is unknown."]}],
            "experiment": {"objective": "Compare against reference assays.", "steps": ["Measure representative powders and compare with reference assays."], "criteria_to_confirm": ["Agree acceptable error and repeatability with the customer."]},
            "requirements": [{"statement": "Assess powder.", "user_quote": quote}]}})


class ScriptedGateway(FormattingGateway):
    """Routes as an assessment, then returns formatter/repair replies in order."""
    def __init__(self, replies, methods=None):
        super().__init__(methods=methods)
        self.replies = list(replies)

    def answer_json(self, messages, schema, timeout, **kwargs):
        if (shortlist := self.shortlist(messages, schema)) is not None:
            return shortlist
        if "route" in schema["properties"]:
            return json.dumps({"route": "technical", "search_query": "NIR powder",
                "wants_experiment": False, "intent": "assessment"})
        assert "evidence_coverage" not in schema["properties"]
        assert "citation_status" not in schema["properties"]
        assert "intent" not in schema["properties"]
        case_schema = schema["properties"]["assessment"]
        assert case_schema["type"] == "object" and "assessment" in schema["required"]
        assert "experiment" in case_schema["required"]
        study_schema = case_schema["properties"]["experiment"]
        assert study_schema["type"] == "object"
        assert study_schema["properties"]["steps"]["minItems"] == 1
        assert study_schema["properties"]["criteria_to_confirm"]["minItems"] == 1
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_valid_assessment_does_not_spend_a_call_on_repair(tmp_path):
    settings, retrieval = fixture(tmp_path)
    gateway = ScriptedGateway([assessment_reply("A draft.")])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="no-review", model=InvestigatingModel())
    answer, _ = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.parts[0].text == "A draft." and gateway.replies == []
    assert [e["issues"] for e in responder.trace if e["stage"] == "verification"] == [[]]


def test_verification_issue_triggers_one_revision(tmp_path):
    settings, retrieval = fixture(tmp_path)
    invented = assessment_reply("The probe withstands 180 °C.")
    gateway = ScriptedGateway([invented, assessment_reply("Revised.")])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="revision", model=InvestigatingModel())
    answer, _ = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.parts[0].text == "Revised."
    checks = [e for e in responder.trace if e["stage"] == "verification" and "issues" in e]
    assert "180 °C" in checks[0]["issues"][0] and checks[1] == {"stage": "verification", "issues": []}


@pytest.mark.parametrize("repair", ["not json", assessment_reply("The probe withstands 180 °C."),
                                  TimeoutError("Repair timed out")])
def test_failed_repair_never_delivers_the_unsupported_draft(tmp_path, repair):
    settings, retrieval = fixture(tmp_path)
    gateway = ScriptedGateway([assessment_reply("The probe withstands 180 °C."), repair])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="repair-failure", model=InvestigatingModel())
    error = TimeoutError if isinstance(repair, TimeoutError) else AnswerValidationError
    with pytest.raises(error):
        responder.answer([{"role": "user", "content": "Assess powder."}])
    assert gateway.replies == []
    assert not any(event["stage"] == "completed" for event in responder.trace)


def test_paraphrased_requirement_quote_requires_model_repair(tmp_path):
    settings, retrieval = fixture(tmp_path)
    gateway = ScriptedGateway([assessment_reply("A draft.", quote="Assess the powder"), assessment_reply("Repaired.")])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="quote-repair", model=InvestigatingModel())
    answer, _ = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.assessment is not None and answer.assessment.requirements[0].user_quote == "Assess powder."
    assert answer.parts[0].text == "Repaired." and gateway.replies == []
    assert any("actual user statement" in e.get("reason", "") for e in responder.trace)
    assert not any(e["stage"] == "answer_softened" for e in responder.trace)


def test_negated_customer_quote_is_never_fuzzily_attached_to_an_opposite_statement(tmp_path):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("A draft."))
    payload["assessment"]["requirements"] = [{"statement": "Contact measurement is allowed.",
        "user_quote": "We can accept contact with the sample."}]
    raw = json.dumps(payload)
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]),
        thread_id="negated-quote", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "We cannot accept contact with the sample."}])
    assert answer.assessment is not None and answer.assessment.requirements == []
    assert sources and "Contact measurement is allowed" not in answer.model_dump_json()
    assert sum("actual user statement" in e.get("reason", "") for e in responder.trace) == 1
    assert any(e["stage"] == "answer_detail_omitted" for e in responder.trace)


@pytest.mark.parametrize("status", ["ready_for_review", "awaiting_clarification", "incomplete"])
@pytest.mark.parametrize("missing", ["steps", "criteria_to_confirm"])
def test_empty_experiment_does_not_become_a_reviewable_plan(tmp_path, missing, status):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("A draft."))
    payload["assessment"]["status"] = status
    payload["assessment"]["experiment"][missing] = []
    raw = json.dumps(payload)
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]),
        thread_id="empty-plan", model=InvestigatingModel())
    with pytest.raises(AnswerValidationError):
        responder.answer([{"role": "user", "content": "Assess powder."}])


def test_quantity_validation_also_protects_normal_qa(tmp_path):
    settings, retrieval = fixture(tmp_path)
    class InventedRating(FormattingGateway):
        def answer_json(self, messages, schema, timeout, **kwargs):
            if "route" in schema["properties"]:
                return super().answer_json(messages, schema, timeout, **kwargs)
            return json.dumps({"parts": [{"kind": "answer", "text": "The probe withstands 3 °C.",
                                          "evidence_ids": ["E1"]}]})
    responder = AgentResponder(retrieval, settings, InventedRating(), thread_id="qa-quantity",
        model=InvestigatingModel())
    with pytest.raises(AnswerValidationError):
        responder.answer([{"role": "user", "content": "What temperature does it withstand?"}])


@pytest.mark.parametrize("query,expected_candidates", [("NIR powder", 1), ("xyzmissing", 0)])
def test_uninspected_results_do_not_imply_absence_from_collection(tmp_path, query, expected_candidates):
    settings, retrieval = fixture(tmp_path)
    # A comparison row is never read automatically by a last-turn search.
    retrieval.items[0].worksheet = "method_comparison"
    retrieval.items[0].structured_data = {"method": "NIR spectroscopy", "measurement_technique": "diffuse reflection probe"}
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="no-evidence",
        model=InvestigatingModel(inspect_candidates=False, query=query))
    answer, sources = responder.answer([{"role": "user", "content": "Which geometry works for powders?"}])
    assert not sources and answer.citation_status == "none"
    text = answer.parts[0].text
    assert "couldn't establish supporting evidence for: NIR powder" in text
    assert "materials don't contain" not in text and "clarify the material" not in text
    assert next(e["candidate_count"] for e in responder.trace if e["stage"] == "search") == expected_candidates


@pytest.mark.parametrize("case,expected", [("citations", "present"), ("structured_gap", "present_with_gaps"),
    ("no_citations", "none")])
def test_citation_metadata_describes_links_not_scientific_support(tmp_path, case, expected):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("Conditional option."))
    payload["citation_status"] = "none"  # The model cannot choose this metadata.
    payload["evidence_coverage"] = "supported"
    if case == "structured_gap":
        payload["assessment"]["unknowns"] = ["Calibration domain is unspecified."]
    elif case == "no_citations":
        payload["parts"] = [{"kind": "gap", "text": "The inspected passage does not establish compatibility."}]
        payload["assessment"]["status"] = "incomplete"
        payload["assessment"]["candidates"] = []
    raw = json.dumps(payload)
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]),
        thread_id="citation-status", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.citation_status == expected
    assert bool(sources) == (case != "no_citations")
    assert "evidence_coverage" not in answer.model_dump()
    assert answer.schema_version == 3



def test_flattened_ocr_exposes_layout_warning_and_related_image(tmp_path):
    from afs_assistant.core.models import Provenance, EvidenceImage
    settings, retrieval = fixture(tmp_path)
    item = retrieval.items[0]
    item.element_ids = ["page-5-ocr"]
    item.provenance = [Provenance(page=5)]
    image = item.model_copy(deep=True)
    image.evidence_id = "slide-image"
    image.element_ids = []
    image.image = EvidenceImage(filename="element-1.png")
    retrieval.items.append(image)
    retrieval.by_id[image.evidence_id] = image
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    record = tools.expose(item.evidence_id, inspected=True)
    assert "range endpoints" in record["layout_warning"]
    assert [tools.aliases[ref] for ref in record["related_images"]] == ["slide-image"]
    assert tools.inspected[record["reference"]]["layout_warning"]



def test_heading_read_cannot_be_used_as_factual_evidence(tmp_path):
    settings, retrieval = fixture(tmp_path)
    item = retrieval.items[0]
    item.element_ids = ["page-5-heading"]
    item.text = "Slide 5: NIR powder methods"
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    record = tools.expose(item.evidence_id, inspected=True)
    assert record["inspected"] is False
    assert "Heading only" in record["scope"]
    assert not tools.inspected


@pytest.mark.parametrize("inline", [False, True])
def test_candidate_description_citations_are_preserved_and_resolved(tmp_path, inline):
    settings, retrieval = fixture(tmp_path)
    other = retrieval.items[0].model_copy(update={"evidence_id": "method-range",
        "text": "The nominal method interval is 900-1700 nm.",
        "locator": Locator(kind="page", label="PDF p. 6")})
    corpus = retrieval.corpus.model_copy(update={"items": [*retrieval.items, other]})
    retrieval = Retrieval({"corpus": corpus.model_dump_json(), "vectors": "[]", "model": ""}, settings=settings)
    payload = json.loads(assessment_reply("A method is worth testing."))
    candidate = payload["assessment"]["candidates"][0]
    candidate["measurement_concept"] = "Nominal interval 900-1700 nm." + (" [E2]" if inline else "")
    candidate["spectral_range"] = "900-1700 nm nominal (source), not instrument coverage."
    if not inline:
        candidate["evidence_ids"] = ["E2"]
    gateway = ScriptedGateway([json.dumps(payload)])
    # The question read takes the powder passage (E1); the gap search reads the range passage (E2).
    responder = AgentResponder(retrieval, settings, gateway, thread_id="candidate-reference",
        model=GapSearchModel(query="nominal interval"))
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.assessment is not None
    candidate = answer.assessment.candidates[0]
    assert candidate.evidence_ids == ["method-range"] and "method-range" in sources
    assert "[E2]" not in candidate.measurement_concept
    assert gateway.replies == [] and not any(e["stage"] == "answer_rejected" for e in responder.trace)


def test_uninspected_inline_candidate_reference_is_never_accepted(tmp_path):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("A method is worth testing."))
    payload["assessment"]["candidates"][0]["measurement_concept"] = "Probe geometry [E99]"
    raw = json.dumps(payload)
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]),
        thread_id="bad-inline-reference", model=InvestigatingModel())
    with pytest.raises(AnswerValidationError):
        responder.answer([{"role": "user", "content": "Assess powder."}])


@pytest.mark.parametrize("field", ["spectral_range", "measurement_concept"])
def test_unresolved_optional_detail_does_not_discard_a_valid_answer(tmp_path, field):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("NIR reflection is worth testing."))
    payload["assessment"]["candidates"][0][field] = "Unestablished value 180 nm."
    raw = json.dumps(payload)
    gateway = ScriptedGateway([raw, raw])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="optional-detail", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.assessment is not None and answer.assessment.experiment is not None
    assert getattr(answer.assessment.candidates[0], field) == ""
    assert answer.parts[0].text == "NIR reflection is worth testing." and sources
    assert answer.citation_status == "present_with_gaps"
    assert any(e["stage"] == "answer_detail_omitted" for e in responder.trace)
    assert gateway.replies == []


def test_only_unmatched_requirement_is_removed_after_failed_quote_repair(tmp_path):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("A source-supported approach is worth testing."))
    payload["assessment"]["requirements"].append({"statement": "An invented condition.",
        "user_quote": "A customer sentence that was never provided."})
    raw = json.dumps(payload)
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]),
        thread_id="partial-quote-recovery", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.assessment is not None
    assert [item.user_quote for item in answer.assessment.requirements] == ["Assess powder."]
    assert "An invented condition" not in answer.model_dump_json() and sources
    assert answer.citation_status == "present_with_gaps"


def test_one_repair_receives_quote_and_quantity_failures_together(tmp_path):
    settings, retrieval = fixture(tmp_path)
    class InspectRepair(ScriptedGateway):
        feedback: str = ""
        def answer_json(self, messages, schema, timeout, **kwargs):
            if "parts" in schema["properties"] and any("Validation failure:" in m["content"] for m in messages):
                self.feedback = messages[-1]["content"]
            return super().answer_json(messages, schema, timeout, **kwargs)
    gateway = InspectRepair([assessment_reply("The probe withstands 180 °C.", quote="Assess the powder"),
                             assessment_reply("A source-supported option is worth testing.")])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="combined-repair", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert "assessment.requirements[0].user_quote" in gateway.feedback
    assert "parts[0].text" in gateway.feedback and "180 °C" in gateway.feedback
    assert "180 °C" not in answer.model_dump_json() and sources
    assert gateway.replies == []


def test_adjacent_context_keeps_independent_references_and_pages(tmp_path):
    from afs_assistant.core.models import EvidenceContext
    settings, retrieval = fixture(tmp_path)
    first = retrieval.items[0].model_copy(update={"parent_context_id": "section"})
    second = first.model_copy(update={"evidence_id": "continuation",
        "text": "A separate continuation describes another interface.",
        "locator": Locator(kind="page", label="PDF p. 6")})
    corpus = retrieval.corpus.model_copy(update={"items": [first, second],
        "contexts": [EvidenceContext(context_id="section", text=first.text+" "+second.text)]})
    retrieval = Retrieval({"corpus": corpus.model_dump_json(), "vectors": "[]", "model": ""}, settings=settings)
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    record = tools.expose(first.evidence_id, inspected=True, context=True)
    assert "surrounding_context" not in record
    adjacent = record["adjacent_evidence"][0]
    assert adjacent["reference"] != record["reference"] and adjacent["location"] == "PDF p. 6"
    assert set(tools.inspected) == {"E1", "E2"}
    assert "continuation" not in tools.inspected["E1"]["text"]
    assert "adjacent_evidence" not in tools.inspected["E1"]
    restored = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    restored.restore([ToolMessage(content=json.dumps(record), tool_call_id="read")])
    assert set(restored.inspected) == {"E1", "E2"}


@pytest.mark.parametrize("field", ["missing.path", "assessment.status", "assessment.requirements[0].user_quote"])
def test_field_citations_cannot_target_non_prose_or_customer_quotes(tmp_path, field):
    from afs_assistant.core.models import FieldCitation
    settings, retrieval = fixture(tmp_path)
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="field-target")
    responder.tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    responder.tools.expose("nir-powder", inspected=True)
    answer = ChatAnswer.model_validate_json(assessment_reply("A candidate."))
    answer.citations = [FieldCitation(field=field, evidence_ids=["E1"])]
    with pytest.raises(ValueError, match="existing generated prose field"):
        responder.validate(answer, [{"role": "user", "content": "Assess powder."}], "assessment")


def test_inline_field_references_are_not_guessed_or_allowed_when_uninspected(tmp_path):
    from afs_assistant.core.citations import normalize_citations
    settings, retrieval = fixture(tmp_path)
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="unknown-field-source")
    responder.tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    responder.tools.expose("nir-powder", inspected=True)
    payload = json.loads(assessment_reply("A candidate."))
    payload["assessment"]["candidates"][0]["limitations"] = ["A limitation. [E99]"]
    answer = normalize_citations(ChatAnswer.model_validate(payload))
    assert answer.citations[0].evidence_ids == ["E99"]
    assert answer.assessment is not None
    assert "[E99]" not in answer.assessment.candidates[0].limitations[0]
    with pytest.raises(ValueError, match="inspected"):
        responder.validate(answer, [{"role": "user", "content": "Assess powder."}], "assessment")


@pytest.mark.parametrize("formatter_intent", [None, "qa"])
def test_routed_assessment_intent_cannot_be_changed_by_formatter(tmp_path, formatter_intent):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("Supported assessment."))
    if formatter_intent is None:
        payload.pop("intent")
    else:
        payload["intent"] = formatter_intent
    gateway = ScriptedGateway([json.dumps(payload)])
    responder = AgentResponder(retrieval, settings, gateway,
        thread_id="routed-intent", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.intent == "assessment" and answer.assessment is not None
    assert sources and gateway.replies == []
    assert not any(e["stage"] == "answer_rejected" for e in responder.trace)


def test_routed_intent_does_not_bypass_missing_assessment_validation(tmp_path):
    settings, retrieval = fixture(tmp_path)
    raw = json.dumps({"parts": [{"kind": "answer", "text": "A response.",
        "evidence_ids": ["E1"]}]})
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]),
        thread_id="missing-assessment", model=InvestigatingModel())
    with pytest.raises(AnswerValidationError):
        responder.answer([{"role": "user", "content": "Assess powder."}])


@pytest.mark.parametrize("path", ["parts[0]", "assessment.candidates[0].claims[0]"])
def test_explicit_text_object_citation_shorthand_needs_no_model_repair(tmp_path, path):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("A supported assessment."))
    payload["citations"] = [{"field": path, "evidence_ids": ["E1"]}]
    gateway = ScriptedGateway([json.dumps(payload)])
    responder = AgentResponder(retrieval, settings, gateway,
        thread_id="text-leaf", model=InvestigatingModel())
    answer, _ = responder.answer([{"role": "user", "content": "Assess powder."}])
    # A field citation of a paragraph/claim text merges into that object's own references.
    assert answer.citations == []
    assert answer.assessment is not None
    target = answer.parts[0] if path == "parts[0]" else answer.assessment.candidates[0].claims[0]
    assert target.evidence_ids == ["nir-powder"]
    assert not any(e["stage"] == "answer_rejected" for e in responder.trace)


def test_paragraph_cited_only_through_field_citations_is_not_rejected(tmp_path):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("A supported assessment."))
    payload["parts"][0]["evidence_ids"] = []
    payload["citations"] = [{"field": "parts[0].text", "evidence_ids": ["E1"]}]
    gateway = ScriptedGateway([json.dumps(payload)])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="slot-merge", model=InvestigatingModel())
    answer, _ = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.parts[0].evidence_ids == ["nir-powder"]
    assert not any(e["stage"] == "answer_rejected" for e in responder.trace)


def test_formatter_separates_customer_facts_evidence_and_latest_question(tmp_path):
    settings, retrieval = fixture(tmp_path)
    guidance = settings.data_dir / "guidance/organizer-guide.json"
    guidance.parent.mkdir(parents=True)
    guidance.write_text(json.dumps({"text": "OBSOLETE_DUPLICATE_GUIDANCE"}))
    captured = []
    class CapturingGateway(FormattingGateway):
        def answer_json(self, messages, schema, timeout, **kwargs):
            if "route" not in schema["properties"]:
                captured.append((messages, schema))
            return super().answer_json(messages, schema, timeout, **kwargs)
    responder = AgentResponder(retrieval, settings, CapturingGateway(), thread_id="input-boundaries",
        model=InvestigatingModel())
    history = [
        {"role": "user", "content": "We monitor a powder with a reflection probe."},
        {"role": "assistant", "content": "UNVERIFIED_EARLIER_GUARANTEE: this hardware is certified."},
        {"role": "user", "content": "Explain its geometry."}]
    answer, _ = responder.answer(history)
    assert answer.intent == "qa" and len(captured) == 1
    messages, schema = captured[0]
    request = json.loads(messages[-1]["content"])
    assert request["earlier_customer_messages"] == [history[0]["content"]]
    assert request["latest_customer_message"] == history[-1]["content"]
    assert request["inspected_evidence"] and request["retrieval_query"]
    assert request["output_schema"] == schema and "conversation" not in request
    assert "UNVERIFIED_EARLIER_GUARANTEE" not in json.dumps(messages)
    assert "OBSOLETE_DUPLICATE_GUIDANCE" not in json.dumps(messages)
    assert schema["properties"]["assessment"] == {"type": "null"}
    assert responder.model_calls == 3  # Routing, one gap turn, one generation.


def test_missing_answer_references_report_the_actual_field_without_attaching_a_source(tmp_path):
    settings, retrieval = fixture(tmp_path)
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="missing-ref")
    responder.tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    responder.tools.expose("nir-powder", inspected=True)
    answer = ChatAnswer.model_validate({"parts": [
        {"kind": "question", "text": "Which matrix?"},
        {"kind": "answer", "text": "A technical conclusion without a source."}]})
    with pytest.raises(ValueError, match=r"parts\[1\].evidence_ids"):
        responder.validate(answer, [{"role": "user", "content": "Explain the method."}], "qa")
    assert answer.parts[1].evidence_ids == []


def test_structural_repair_collects_all_errors_before_mutating_references(tmp_path):
    from afs_assistant.core.models import FieldCitation
    settings, retrieval = fixture(tmp_path)
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="all-structure")
    responder.tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    responder.tools.expose("nir-powder", inspected=True)
    answer = ChatAnswer.model_validate_json(assessment_reply("A draft.", quote="A statement the user never made."))
    answer.parts[0].evidence_ids = []
    assert answer.assessment and answer.assessment.experiment
    answer.assessment.experiment.steps = []
    answer.citations = [FieldCitation(field="missing.field", evidence_ids=["E99"])]
    before = answer.model_dump_json()
    with pytest.raises(ValueError) as error:
        responder.validate(answer, [{"role": "user", "content": "Assess powder."}], "assessment")
    feedback = str(error.value)
    assert all(fragment in feedback for fragment in (
        "parts[0].evidence_ids", "assessment.requirements[0].user_quote",
        "first-study objective", "missing.field", "Only inspected evidence"))
    assert answer.model_dump_json() == before


def test_question_only_qa_requires_a_direct_response_without_inventing_support(tmp_path):
    class ClarificationDraft(FormattingGateway):
        def __init__(self):
            super().__init__()
            self.generations = 0
        def answer_json(self, messages, schema, timeout, **kwargs):
            if "route" in schema["properties"]:
                return super().answer_json(messages, schema, timeout, **kwargs)
            self.generations += 1
            if self.generations == 1:
                return json.dumps({"parts": [{"kind": "question", "text": "Which assay do you use?"}]})
            assert "cannot replace" in messages[-1]["content"]
            return json.dumps({"parts": [{"kind": "gap",
                "text": "The inspected material does not establish a validated prediction error."}]})
    settings, retrieval = fixture(tmp_path)
    gateway = ClarificationDraft()
    responder = AgentResponder(retrieval, settings, gateway,
        thread_id="direct-response", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Does this material establish a validated prediction error?"}])
    assert answer.parts[0].kind == "gap" and sources == {}
    assert gateway.generations == 2


def test_final_attempt_omits_only_the_uncited_paragraph(tmp_path):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("Supported finding."))
    payload["parts"].append({"kind": "answer", "text": "An unsupported extra statement.", "evidence_ids": []})
    raw = json.dumps(payload)
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]), thread_id="omit-part", model=InvestigatingModel())
    answer, _ = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert [part.text for part in answer.parts] == ["Supported finding."]
    assert any(e["stage"] == "answer_detail_omitted" and e["fields"] == ["parts[1]"] for e in responder.trace)
    assert answer.citation_status == "present_with_gaps"


def test_final_attempt_still_fails_when_nothing_cited_remains(tmp_path):
    settings, retrieval = fixture(tmp_path)
    raw = json.dumps({"parts": [{"kind": "answer", "text": "Unsupported.", "evidence_ids": []}],
        "assessment": json.loads(assessment_reply("x"))["assessment"] | {"candidates": [], "status": "incomplete"}})
    responder = AgentResponder(retrieval, settings, ScriptedGateway([raw, raw]), thread_id="omit-all", model=InvestigatingModel())
    with pytest.raises(AnswerValidationError):
        responder.answer([{"role": "user", "content": "Assess powder."}])


def test_paragraph_markers_become_inline_source_numbers_without_raw_aliases(tmp_path):
    settings, retrieval = fixture(tmp_path)
    payload = json.loads(assessment_reply("x"))
    payload["parts"] = [{"kind": "answer", "text": "NIR reflection suits powders [E1]. We recommend comparing it first.",
                         "evidence_ids": []},
                        {"kind": "question", "text": "Which API range applies [E1]?", "evidence_ids": []}]
    responder = AgentResponder(retrieval, settings, ScriptedGateway([json.dumps(payload)]), thread_id="inline",
                               model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.parts[0].text == "NIR reflection suits powders [1]. We recommend comparing it first."
    assert answer.parts[0].evidence_ids == ["nir-powder"] and list(sources) == ["nir-powder"]
    assert answer.parts[1].text == "Which API range applies?"
    assert "[E" not in json.dumps(answer.model_dump())


def test_a_paragraph_may_carry_more_than_six_inline_sources():
    from afs_assistant.core.models import AnswerPart
    assert len(AnswerPart(kind="answer", text="x", evidence_ids=[f"E{i}" for i in range(1, 10)]).evidence_ids) == 9


def background_fixture(tmp_path):
    """An application passage plus S01 method sections, an overview paragraph and a table row."""
    from afs_assistant.core.models import Provenance
    settings, retrieval = fixture(tmp_path)
    filler = " Practical limits and calibration needs are described here." * 3
    def add(eid, text, heading="", page=10, data=None):
        item = retrieval.items[0].model_copy(deep=True)
        item.evidence_id, item.text, item.heading_path = eid, text, [heading] if heading else []
        item.provenance, item.locator = [Provenance(page=page)], Locator(kind="page", label=f"PDF p. {page}")
        if data:
            item.worksheet, item.structured_data = "method_comparison", data
        retrieval.items.append(item)
        retrieval.by_id[eid] = item
    add("nir-section", "NIR spectra (780-2500 nm) contain overtones." + filler, "5.2. Near-IR Spectrometry")
    add("raman-section", "The Raman shift is expressed in wavenumbers." + filler, "5.3. Raman Spectroscopy", page=11)
    add("nir-overview", "NIR offers deep penetration for scattering samples." + filler, "3.1. Information Capability", page=5)
    add("nir-row", "NIR row", page=6, data={"method": "NIR spectroscopy", "measurement_technique": "diffuse reflection probe"})
    return settings, retrieval


def inspected_ids(tools):
    return {record["evidence_id"] for record in tools.inspected.values()}


def test_background_is_read_only_for_shortlisted_methods(tmp_path):
    settings, retrieval = background_fixture(tmp_path)
    gateway = ScriptedGateway([assessment_reply("A draft.")], methods=["NIR"])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="background", model=InvestigatingModel())
    responder.answer([{"role": "user", "content": "Assess powder."}])
    added = next(e for e in responder.trace if e["stage"] == "background_added")
    assert added["methods"] == ["NIR"] and added["references"]
    assert {"nir-section", "nir-row", "nir-overview"} <= inspected_ids(responder.tools)
    assert "raman-section" not in inspected_ids(responder.tools)
    request = gateway.shortlist_requests[0]
    assert request["customer_messages"] == ["Assess powder."] and request["inspected_evidence"]


def test_background_keeps_each_methods_own_section_first_under_budget(tmp_path):
    settings, retrieval = background_fixture(tmp_path)
    sizing = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    for eid in ("nir-section", "raman-section"):
        sizing.expose(eid, inspected=True)
    budget = len(json.dumps(sizing.inspected, ensure_ascii=False).encode()) + 10
    from dataclasses import replace
    tools = InvestigationTools(retrieval, replace(settings, evidence_byte_budget=budget), None, [], time.monotonic()+90)
    tools.background_headroom = 0  # Isolate tier ordering from the background allowance.
    added = tools.add_method_background(["NIR", "Raman", "NIR"])
    assert [tools.aliases[ref] for ref in added] == ["nir-section", "raman-section"]


def test_background_has_its_own_headroom_when_agent_filled_the_budget(tmp_path):
    settings, retrieval = background_fixture(tmp_path)
    sizing = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    sizing.expose("nir-powder", inspected=True)
    full = len(json.dumps(sizing.inspected, ensure_ascii=False).encode())
    from dataclasses import replace
    tools = InvestigationTools(retrieval, replace(settings, evidence_byte_budget=full), None, [], time.monotonic()+90)
    tools.expose("nir-powder", inspected=True)
    added = tools.add_method_background(["NIR"])
    assert "nir-section" in [tools.aliases[ref] for ref in added]
    assert tools.budget_headroom == 0
    with pytest.raises(ValueError, match="budget"):
        tools.expose("raman-section", inspected=True)  # The agent's own reads stay within its budget.


def test_overview_precedes_rows_and_rows_are_process_interfaces(tmp_path):
    settings, retrieval = background_fixture(tmp_path)
    for eid, technique in [("nir-cuvette", "cuvette"), ("nir-sphere", "integrating sphere"),
                           ("nir-transflection", "transmission or transflection probe")]:
        row = retrieval.by_id["nir-row"].model_copy(deep=True)
        row.evidence_id, row.structured_data = eid, {"method": "NIR spectroscopy", "measurement_technique": technique}
        retrieval.items.append(row)
        retrieval.by_id[eid] = row
    tools = InvestigationTools(retrieval, settings, None, [], time.monotonic()+90)
    added = [tools.aliases[ref] for ref in tools.add_method_background(["NIR"])]
    assert added == ["nir-section", "nir-overview", "nir-row", "nir-transflection"]


@pytest.mark.parametrize("shortlist", ["empty", "error"])
def test_empty_or_failed_shortlist_still_answers_from_agent_evidence(tmp_path, shortlist):
    settings, retrieval = background_fixture(tmp_path)
    class Gateway(ScriptedGateway):
        def shortlist(self, messages, schema):
            if "methods" in schema["properties"] and shortlist == "error":
                raise TimeoutError("Shortlist timed out")
            return super().shortlist(messages, schema)
    responder = AgentResponder(retrieval, settings, Gateway([assessment_reply("A draft.")]),
        thread_id=f"shortlist-{shortlist}", model=InvestigatingModel())
    answer, sources = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.parts[0].text == "A draft." and sources
    assert "nir-section" not in inspected_ids(responder.tools) or shortlist == "empty"
    if shortlist == "error":
        assert any(e.get("status") == "failed" and e["stage"] == "method_shortlist" for e in responder.trace)
    else:
        assert next(e for e in responder.trace if e["stage"] == "background_added")["references"] == []


def test_narrow_question_never_asks_for_a_method_shortlist(tmp_path):
    settings, retrieval = background_fixture(tmp_path)
    gateway = FormattingGateway(methods=["NIR"])
    responder = AgentResponder(retrieval, settings, gateway, thread_id="qa-no-shortlist", model=InvestigatingModel())
    responder.answer([{"role": "user", "content": "Which geometry works for powders?"}])
    assert gateway.shortlist_requests == []
    assert not any(e["stage"] in {"method_shortlist", "background_added"} for e in responder.trace)


def test_answer_effort_applies_only_to_writing_the_answer(tmp_path):
    from dataclasses import replace
    settings, retrieval = background_fixture(tmp_path)
    efforts = {}
    class Gateway(ScriptedGateway):
        def answer_json(self, messages, schema, timeout, **kwargs):
            kind = "methods" if "methods" in schema["properties"] else "route" if "route" in schema["properties"] else "answer"
            efforts.setdefault(kind, kwargs.get("reasoning_effort"))
            return super().answer_json(messages, schema, timeout, **kwargs)
    responder = AgentResponder(retrieval, replace(settings, answer_reasoning_effort="low"),
        Gateway([assessment_reply("A draft.")], methods=["NIR"]), thread_id="answer-effort", model=InvestigatingModel())
    responder.answer([{"role": "user", "content": "Assess powder."}])
    assert efforts == {"route": None, "methods": None, "answer": "low"}


@pytest.mark.parametrize("first_failure", ["cut_off", "unsupported"])
def test_only_a_cut_off_answer_gets_the_concise_repair(tmp_path, first_failure):
    from afs_assistant.core.gateway import TruncatedAnswerError
    settings, retrieval = fixture(tmp_path)
    repairs = []
    class Gateway(ScriptedGateway):
        def answer_json(self, messages, schema, timeout, **kwargs):
            if "route" not in schema["properties"] and "methods" not in schema["properties"] and len(messages) > 2:
                repairs.append(messages[-1]["content"])
            return super().answer_json(messages, schema, timeout, **kwargs)
    first = (TruncatedAnswerError("Structured answer did not finish normally") if first_failure == "cut_off"
             else assessment_reply("The probe withstands 180 °C."))
    responder = AgentResponder(retrieval, settings, Gateway([first, assessment_reply("Revised.")]),
        thread_id=f"repair-{first_failure}", model=InvestigatingModel())
    answer, _ = responder.answer([{"role": "user", "content": "Assess powder."}])
    assert answer.parts[0].text == "Revised." and len(repairs) == 1
    assert ("cut off at the output limit" in repairs[0]) == (first_failure == "cut_off")


def test_tool_arguments_are_recorded_in_the_trace(tmp_path):
    settings, retrieval = fixture(tmp_path)
    responder = AgentResponder(retrieval, settings, FormattingGateway(), thread_id="tool-args", model=InvestigatingModel())
    responder.answer([{"role": "user", "content": "Which geometry works for powders?"}])
    search = next(e for e in responder.trace if e["stage"] == "tool" and e["tool"] == "search_knowledge")
    assert search["arguments"]["query"] == "NIR powder"
