from afs_assistant.core.models import (AnswerPart, Assessment, Candidate, ChatAnswer, Claim, Experiment,
    FieldCitation)
from afs_assistant.workflow.omission import omit_failing_fields


def case():
    candidates = [Candidate(name=name, claims=[Claim(text=f"{name} a.", evidence_ids=["E1"]),
                  Claim(text=f"{name} b.", evidence_ids=["E1"])], assumptions=[], limitations=["l1", "l2"])
                  for name in ("NIR", "Raman")]
    return ChatAnswer(intent="assessment",
        parts=[AnswerPart(kind="answer", text="p0", evidence_ids=["E1"]), AnswerPart(kind="answer", text="p1"),
               AnswerPart(kind="proposal", text="p2")],
        citations=[FieldCitation(field="parts[2].text", evidence_ids=["E1"]),
                   FieldCitation(field="assessment.candidates[1].limitations[1]", evidence_ids=["E1"])],
        assessment=Assessment(status="ready_for_review", summary="s", candidates=candidates,
            experiment=Experiment(objective="o", steps=["s1"], criteria_to_confirm=["c1"])))


def test_failing_paragraphs_and_claims_are_removed_whole_and_citations_reindexed():
    answer = case()
    omitted = omit_failing_fields(answer, [1], ["assessment.candidates[1].claims[0].text: unsupported 9 nm",
                                                "assessment.candidates[1].limitations[0]: unsupported 3 °C"])
    assert omitted is not None and set(omitted) == {"parts[1]", "assessment.candidates[1].claims[0].text",
                                                    "assessment.candidates[1].limitations[0]"}
    assert [p.text for p in answer.parts] == ["p0", "p2"]
    assert answer.assessment is not None and [c.text for c in answer.assessment.candidates[1].claims] == ["Raman b."]
    assert {c.field for c in answer.citations} == {"parts[1].text", "assessment.candidates[1].limitations[0]"}


def test_a_candidate_without_claims_is_removed_and_required_fields_are_never_emptied():
    answer = case()
    assert omit_failing_fields(answer, [], ["assessment.candidates[0].claims[0].text: x",
                                            "assessment.candidates[0].claims[1].text: y"]) is not None
    assert answer.assessment is not None and [c.name for c in answer.assessment.candidates] == ["Raman"]
    assert omit_failing_fields(case(), [], ["assessment.experiment.steps[0]: 5 samples"]) is None
    assert omit_failing_fields(case(), [], ["assessment.summary: 180 °C"]) is None


def test_fields_of_a_candidate_removed_with_its_last_claim_are_skipped():
    answer = case()
    omitted = omit_failing_fields(answer, [], ["assessment.candidates[1].claims[0].text: x",
        "assessment.candidates[1].claims[1].text: y", "assessment.candidates[1].spectral_range: 180 nm"])
    assert omitted is not None and answer.assessment is not None
    assert [c.name for c in answer.assessment.candidates] == ["NIR"]
