import pytest

from afs_assistant.core.models import AnswerPart, Assessment, Candidate, ChatAnswer, Claim, Experiment, Question, Requirement
from afs_assistant.workflow.verify import verify


def answer(claim="Silica fibers transmit 350-2400 nm.", refs=("E1",)):
    return ChatAnswer(parts=[AnswerPart(kind="answer", text=claim, evidence_ids=list(refs))])


INSPECTED = {"E1": {"text": "Standard VIS-NIR silica fibers: 350 nm to 2400 nm."}}


def test_explicit_values_and_compatible_unit_conversions_pass():
    assert verify(answer(), "", INSPECTED) == []
    assert verify(answer("Silica fibers reach 2.4 µm."), "", INSPECTED) == []
    assert verify(answer("The wavelength is 2,400 nm."), "", INSPECTED) == []


@pytest.mark.parametrize("claim,source", [
    ("The probe withstands 180 °C.", "The wavelength is 180 nm."),
    ("The probe withstands 3 °C.", "No temperature rating is provided."),
    ("Detection reaches 0.05%.", "A 0.5 mm path was used."),
    ("The wavelength is 180 nm.", "The wavelength is 1800 nm."),
    ("The wavelength is 180 nm.", "Only a bare value: 180."),
    ("The wavelength is 2.5 µm.", "The wavelength is 4000 cm-1."),
])
def test_wrong_units_small_integers_and_arbitrary_scaling_cannot_authorize_specs(claim, source):
    assert verify(answer(claim), "", {"E1": {"text": source}})


def test_only_the_claims_cited_passages_supply_technical_values():
    inspected = {"E1": {"text": "No temperature rating.", "evidence_id": "temperature-180",
                         "title": "180 °C", "source_version": "180"},
                 "E2": {"text": "The probe is rated at 180 °C."}}
    assert verify(answer("The probe withstands 180 °C."), "Our process is at 180 °C.", inspected)
    assert verify(answer("The probe withstands 180 °C.", refs=("E2",)), "", inspected) == []


def test_ambiguous_ocr_and_search_descriptions_cannot_authorize_exact_values():
    record = {"text": "Fiber A 350 nm 2400 nm", "layout_warning": "Label association unresolved.",
              "visual_description": "The endpoint is 2400 nm."}
    assert verify(answer("The endpoint is 2400 nm."), "", {"E1": record})
    record["visual_observation"] = "Fiber A extends from 350 nm to 2400 nm."
    assert verify(answer("The endpoint is 2400 nm."), "", {"E1": record}) == []


def test_source_rows_and_range_endpoints_are_read_without_metadata():
    assert verify(answer("The wavelength is 350–2400 nm."), "", {
        "E1": {"table_row": {"coverage": "350–2400 nm"}}}) == []
    assert verify(answer("The temperature is -10 to 30 °C."), "", {
        "E1": {"text": "Temperature: -10 to 30 °C"}}) == []


def case_answer():
    reply = answer("Silica fibers transmit 350-2400 nm.")
    reply.intent = "assessment"
    reply.assessment = Assessment(status="ready_for_review", summary="Compare candidates.",
        candidates=[Candidate(name="NIR", evidence_ids=["E1"], measurement_concept="Transmission.",
            spectral_range="350-2400 nm nominal source range.",
            claims=[Claim(text="Silica fibers transmit 350-2400 nm.", evidence_ids=["E1"])],
            assumptions=[], limitations=["Coverage needs confirmation."])],
        questions=[Question(field="conditions", question="What is the operating temperature?", affects="Probe compatibility.")],
        experiment=Experiment(objective="Compare against reference assays.",
            steps=["Measure representative samples."], criteria_to_confirm=["Agree the acceptable error with the customer."]),
        requirements=[Requirement(statement="Process at 3 °C.", user_quote="Our process is at 3 °C.")])
    return reply


def test_customer_requirements_are_not_hardware_ratings():
    reply = case_answer()
    assert verify(reply, "Our process is at 3 °C.", INSPECTED) == []
    reply.parts[0].text = "The probe withstands 3 °C."
    assert verify(reply, "Our process is at 3 °C.", INSPECTED)


@pytest.mark.parametrize("field", ["summary", "range", "concept", "claim", "assumption", "limitation",
                                   "question", "affects", "objective", "step", "criterion", "requirement"])
def test_quantity_checks_cover_structured_exports_and_proposals(field):
    reply = case_answer()
    case = reply.assessment
    assert case is not None and case.experiment is not None
    value = "Unestablished value of 180 °C."
    if field == "summary":
        case.summary = value
    elif field == "range":
        case.candidates[0].spectral_range = value
    elif field == "concept":
        case.candidates[0].measurement_concept = value
    elif field == "claim":
        case.candidates[0].claims[0].text = value
    elif field == "assumption":
        case.candidates[0].assumptions = [value]
    elif field == "limitation":
        case.candidates[0].limitations = [value]
    elif field == "question":
        case.questions[0].question = value
    elif field == "affects":
        case.questions[0].affects = value
    elif field == "objective":
        case.experiment.objective = value
    elif field == "step":
        case.experiment.steps = [value]
    elif field == "criterion":
        case.experiment.criteria_to_confirm = [value]
    else:
        case.requirements[0].statement = value
    assert verify(reply, "Our process is at 3 °C.", INSPECTED)


def test_default_sample_counts_are_not_exempt_small_integers():
    reply = case_answer()
    assert reply.assessment is not None and reply.assessment.experiment is not None
    reply.assessment.experiment.steps = ["Measure 5 samples."]
    assert verify(reply, "Our process is at 3 °C.", INSPECTED)


def test_candidate_metadata_citations_support_descriptive_fields_only():
    reply = case_answer()
    assert reply.assessment is not None
    candidate = reply.assessment.candidates[0]
    candidate.measurement_concept = "Nominal interval 900-1700 nm."
    candidate.spectral_range = "900-1700 nm nominal (source)."
    sources = {**INSPECTED, "E2": {"text": "The nominal method interval is 900-1700 nm."}}
    issues = verify(reply, "Our process is at 3 °C.", sources)
    assert any("assessment.candidates[0].measurement_concept:" in issue for issue in issues)
    candidate.evidence_ids = ["E2"]
    assert verify(reply, "Our process is at 3 °C.", sources) == []
    # A candidate-level citation cannot authorize an independently cited claim.
    candidate.claims[0].text = "The nominal interval is 900-1700 nm."
    assert any("assessment.candidates[0].claims[0].text:" in issue
               for issue in verify(reply, "Our process is at 3 °C.", sources))


def test_diagnostics_identify_all_failed_fields_without_scenario_rules():
    reply = case_answer()
    assert reply.assessment is not None and reply.assessment.experiment is not None
    reply.assessment.candidates[0].spectral_range = "180 nm."
    reply.assessment.experiment.steps = ["Measure 5 samples."]
    issues = verify(reply, "", INSPECTED)
    assert any(issue.startswith("assessment.candidates[0].spectral_range:") for issue in issues)
    assert any(issue.startswith("assessment.experiment.steps[0]:") for issue in issues)


def test_published_study_settings_cannot_become_customer_experiment_targets():
    reply = case_answer()
    assert reply.assessment is not None and reply.assessment.experiment is not None
    reply.assessment.experiment.steps = ["Measure at 3 °C using 5 samples."]
    sources = {"E1": {"text": "Silica fibers transmit 350-2400 nm. Study used 5 samples at 3 °C."}}
    issues = verify(reply, "Our process is at 3 °C.", sources)
    assert any("5 samples" in issue and "not supplied by the customer" in issue for issue in issues)
    assert verify(reply, "Our process is at 3 °C. Use 5 samples.", sources) == []


def test_structured_field_reference_authorizes_only_that_specific_field():
    from afs_assistant.core.models import FieldCitation
    reply = case_answer()
    assert reply.assessment is not None
    reply.assessment.candidates[0].limitations = ["Source condition 180 °C.", "Another condition 180 °C."]
    reply.citations = [FieldCitation(field="assessment.candidates[0].limitations[0]", evidence_ids=["E2"])]
    sources = {**INSPECTED, "E2": {"text": "Reported condition: 180 °C."}}
    issues = verify(reply, "Our process is at 3 °C.", sources)
    assert not any("limitations[0]" in issue for issue in issues)
    assert any("limitations[1]" in issue for issue in issues)


@pytest.mark.parametrize("precise_reference", [False, True])
def test_optical_depth_rows_cannot_authorize_spectral_coverage(precise_reference):
    from afs_assistant.core.models import FieldCitation
    reply = case_answer()
    assert reply.assessment is not None
    candidate = reply.assessment.candidates[0]
    candidate.evidence_ids = ["E2"]
    candidate.measurement_concept = "Sampling depth 1-6 um."
    candidate.spectral_range = "Nominal spectral coverage 1-6 um."
    if precise_reference:
        reply.citations = [FieldCitation(
            field="assessment.candidates[0].spectral_range", evidence_ids=["E2"])]
    inspected = {**INSPECTED, "E2": {
        "text": "Interface sampling depth: 1-6 um.",
        "table_row": {"measurement_technique": "contact interface",
                      "optical_path_or_depth": "1-6 um", "sensitivity": "average"}}}
    issues = verify(reply, "Our process is at 3 °C.", inspected)
    assert any("spectral_range:" in issue for issue in issues)
    assert not any("measurement_concept:" in issue for issue in issues)
    # A separate actual spectral passage may support the range; the geometry
    # row remains usable for the independently supported sampling depth.
    inspected["E3"] = {"text": "Nominal spectral coverage: 1-6 um."}
    reply.citations = [FieldCitation(
        field="assessment.candidates[0].spectral_range", evidence_ids=["E3"])]
    assert verify(reply, "Our process is at 3 °C.", inspected) == []


def test_unrelated_candidate_claim_cannot_authorize_a_wrongly_cited_range():
    from afs_assistant.core.models import FieldCitation
    reply = case_answer()
    assert reply.assessment is not None
    candidate = reply.assessment.candidates[0]
    candidate.evidence_ids = ["E2"]
    inspected = {**INSPECTED, "E2": {"text": "The comparison describes scattering geometry only."}}
    issues = verify(reply, "Our process is at 3 °C.", inspected)
    assert any(issue.startswith("assessment.candidates[0].spectral_range:") for issue in issues)
    reply.citations = [FieldCitation(field="assessment.candidates[0].spectral_range", evidence_ids=["E1"])]
    assert verify(reply, "Our process is at 3 °C.", inspected) == []


def test_source_settings_cannot_be_prescribed_in_visible_proposals():
    from afs_assistant.core.models import FieldCitation
    reply = answer("The reported trial used 12 samples.")
    reply.parts.append(AnswerPart(kind="proposal", text="Use 12 samples.", evidence_ids=["E1"]))
    reply.citations = [FieldCitation(field="parts[1].text", evidence_ids=["E1"])]
    inspected = {"E1": {"text": "The reported trial used 12 samples."}}
    issues = verify(reply, "", inspected)
    assert len(issues) == 1 and issues[0].startswith("parts[1].text:")
    assert "not supplied by the customer" in issues[0]
    assert verify(reply, "We can use 12 samples.", inspected) == []


@pytest.mark.parametrize("kind", ["gap", "question"])
def test_cited_gap_or_question_can_repeat_customer_target_without_authorizing_performance(kind):
    from afs_assistant.core.models import FieldCitation
    text = ("The evidence does not establish the requested error below 0.1%."
            if kind == "gap" else "Does the requested 0.1% refer to relative error?")
    reply = ChatAnswer(parts=[AnswerPart(kind=kind, text=text, evidence_ids=["E1"])],
        citations=[FieldCitation(field="parts[0].text", evidence_ids=["E1"])])
    inspected = {"E1": {"text": "The application describes a reflection measurement concept."}}
    user = "Can you guarantee an error below 0.1%?"
    assert verify(reply, user, inspected) == []
    reply.parts[0].kind = "answer"
    reply.parts[0].text = "The equipment achieves an error below 0.1%."
    assert verify(reply, user, inspected)


def test_modifiers_and_bare_ratios_cannot_hide_unverified_study_numbers():
    reply = case_answer()
    assert reply.assessment and reply.assessment.experiment
    reply.assessment.experiment.steps = [
        "Collect 20-30 representative powder samples.",
        "Split the data 70:30."]
    issues = verify(reply, "Our process is at 3 °C.", INSPECTED)
    assert any("steps[0]" in issue and "numerical study wording" in issue for issue in issues)
    assert any("steps[1]" in issue and "numerical study wording" in issue for issue in issues)
    reply.assessment.experiment.steps = ["1. Use PLS2 on representative samples."]
    assert verify(reply, "Our process is at 3 °C.", INSPECTED) == []
    reply.assessment.experiment.steps = ["Collect 20-30 samples."]
    assert verify(reply, "Our process is at 3 °C. We can use 20-30 samples.", INSPECTED) == []


@pytest.mark.parametrize("step", [
    "Compare NIR (350-2400 nm) with Raman on the same blends.",
    "Use the variables listed in Fig. 7 as candidate targets.",
    "Screen both streams in phase 2 after the laboratory comparison.",
])
def test_study_steps_may_describe_cited_ranges_and_label_figures_or_phases(step):
    reply = case_answer()
    assert reply.assessment is not None and reply.assessment.experiment is not None
    reply.assessment.experiment.steps = [step]
    assert verify(reply, "Our process is at 3 °C.", INSPECTED) == []


@pytest.mark.parametrize("step", [
    "Compare NIR at 900-1700 nm with Raman.",   # not in any cited passage
    "Measure 30 samples per blend.",             # a count the customer did not supply
    "Accept errors below 2 %.",                  # an invented acceptance target
])
def test_study_steps_still_reject_uncited_ranges_and_invented_settings(step):
    reply = case_answer()
    assert reply.assessment is not None and reply.assessment.experiment is not None
    reply.assessment.experiment.steps = [step]
    assert verify(reply, "Our process is at 3 °C.", INSPECTED)


def test_inline_enumerators_are_not_study_settings():
    reply = answer("We propose comparing (1) NIR alone, (2) Raman alone and 3) both together.")
    reply.parts[0].kind = "proposal"
    reply.parts[0].evidence_ids = []
    assert verify(reply, "", INSPECTED) == []


def test_cited_passage_must_concern_the_fields_method():
    sources = {"E1": {"text": "UV-Vis transmission saturates in turbid media."},
               "E2": {"text": "NIR transmission fails when milk becomes too thick."}}
    assert any("concerns UV-Vis, not NIR" in issue
               for issue in verify(answer("NIR transmission saturates in turbid whey.", ("E1",)), "", sources))
    assert verify(answer("NIR transmission saturates in turbid whey.", ("E2",)), "", sources) == []


def test_unnamed_claims_inherit_their_candidates_method():
    reply = case_answer()
    assert reply.assessment is not None
    reply.assessment.candidates[0].name = "Combined UV-Vis and Raman"
    reply.assessment.candidates[0].claims[0].text = "Combining methods can solve problems one method cannot."
    sources = {"E1": {"text": "The combination of NIR spectroscopy with fluorescence spectroscopy improved biomass determination."}}
    assert any("claims[0].text: cited E1 concerns NIR, fluorescence" in issue
               for issue in verify(reply, "Our process is at 3 °C.", sources))


def test_process_fields_cannot_cite_cuvette_or_other_geometry_rows():
    cuvette = {"E1": {"table_row": {"method": "NIR spectroscopy", "measurement_technique": "cuvette",
                                    "physical_phenomenon": "transmission"}}}
    issues = verify(answer("A NIR diffuse reflection probe is flush-mounted in the pipe."), "", cuvette)
    assert any("laboratory cuvette row" in issue for issue in issues)
    transmission = {"E1": {"table_row": {"method": "NIR spectroscopy",
        "measurement_technique": "transmission or transflection probe", "physical_phenomenon": "transmission"}}}
    issues = verify(answer("A NIR diffuse reflection probe suits powders."), "", transmission)
    assert any("describes transmission geometry, but the field describes reflection" in issue for issue in issues)


def test_single_source_settings_are_not_customer_configuration():
    reply = case_answer()
    assert reply.assessment is not None
    reply.assessment.candidates[0].evidence_ids = ["E1"]
    reply.assessment.candidates[0].measurement_concept = "Raman contact probe with 785 nm excitation."
    sources = {"E1": {"text": "Raman fiber probe for contact measurements with 785 nm excitation wavelength."}}
    issues = verify(reply, "Our process is at 3 °C.", sources)
    assert any("single source settings (785 nm)" in issue for issue in issues)
    reply.assessment.candidates[0].measurement_concept = "Raman contact probe (source example: 785 nm excitation)."
    assert not any("single source settings" in issue for issue in verify(reply, "Our process is at 3 °C.", sources))


@pytest.mark.parametrize("claim,source", [
    ("The ester band is at 1740 cm⁻¹.", "the ester carbonyl band at about 1740 cm -1 is characteristic"),
    ("The ester band is at 1740 cm-1.", "the ester carbonyl band at about 1740 cm^-1"),
])
def test_spaced_and_caret_wavenumbers_match(claim, source):
    assert verify(answer(claim), "", {"E1": {"text": source}}) == []


def test_alphanumeric_grades_are_not_volumes_or_lengths():
    assert verify(answer("The shaft is 316L stainless steel."), "", INSPECTED) == []
    assert verify(answer("The cable is 5 m long."), "", INSPECTED)


def test_range_bounds_are_not_hardware_settings():
    reply = case_answer()
    assert reply.assessment is not None
    reply.assessment.candidates[0].evidence_ids = ["E1"]
    reply.assessment.candidates[0].measurement_concept = "Silica fiber link transmitting up to 2400 nm."
    assert not any("single source settings" in issue for issue in verify(reply, "Our process is at 3 °C.", INSPECTED))
