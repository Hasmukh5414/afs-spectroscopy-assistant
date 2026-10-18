from afs_assistant.core.reporting import assessment_markdown


def test_same_page_passages_remain_distinct_and_page_ranges_are_preserved():
    payload = {"question": "Compare interfaces.", "release": "fixture",
        "answer": {"parts": [
            {"kind": "answer", "text": "One interface.", "evidence_ids": ["a"]},
            {"kind": "answer", "text": "Another interface.", "evidence_ids": ["b"]}]},
        "sources": {
            "a": {"source_id": "S01", "locator": {"label": "PDF pp. 4–5"},
                  "title": "Methods", "text": "First supporting passage."},
            "b": {"source_id": "S01", "locator": {"label": "PDF pp. 4–5"},
                  "title": "Methods", "text": "Different supporting passage."}}}
    report = assessment_markdown(payload)
    assert "One interface. [1]" in report and "Another interface. [2]" in report
    assert "1. S01 PDF pp. 4–5" in report and "2. S01 PDF pp. 4–5" in report
    assert "First supporting passage." in report and "Different supporting passage." in report


def test_risks_are_grouped_by_approach_and_repeats_are_dropped():
    def candidate(name, limitations, assumptions=()):
        return {"name": name, "claims": [{"text": name + " is a candidate.", "evidence_ids": []}],
                "limitations": list(limitations), "assumptions": list(assumptions)}
    payload = {"question": "Assess.", "release": "fixture", "sources": {}, "answer": {
        "parts": [{"kind": "answer", "text": "Summary.", "evidence_ids": []}],
        "assessment": {"status": "ready_for_review", "summary": "Summary.", "questions": [], "requirements": [],
            "candidates": [
                candidate("NIR", ["Requires a new calibration for the new API/excipient matrix; old calibration is invalid."]),
                candidate("Raman", ["Requires a new calibration for the new matrix.", "Higher cost than NIR."],
                          ["The blend does not fluoresce strongly."])],
            "gaps": ["Higher cost than NIR.", "Reference assay uncertainty is unknown."]}}}
    report = assessment_markdown(payload)
    assert report.count("new calibration") == 1 and report.count("Higher cost") == 1
    assert "**Assumptions to confirm:**" in report and "The blend does not fluoresce strongly." in report
    assert "## Shared risks and evidence gaps" in report and "Reference assay uncertainty is unknown." in report
    assert "Risks and uncertainty" not in report


def test_inline_numbers_are_not_repeated_and_gap_paragraphs_are_not_restated():
    payload = {"question": "Assess.", "release": "fixture", "sources": {
        "a": {"source_id": "S01", "locator": {"label": "PDF p. 15"}, "title": "Methods", "text": "Passage."}},
        "answer": {"parts": [{"kind": "answer", "text": "Raman adds backbone information [1].", "evidence_ids": ["a"]},
                             {"kind": "gap", "text": "Probe temperature ratings are not stated.", "evidence_ids": []}],
                   "assessment": {"status": "ready_for_review", "summary": "s", "questions": [], "requirements": [],
                                  "candidates": [], "gaps": ["Probe temperature ratings are not stated."]}}}
    report = assessment_markdown(payload)
    assert "Raman adds backbone information [1]." in report and "[1]. [1]" not in report
    assert report.count("Probe temperature ratings are not stated.") == 1
