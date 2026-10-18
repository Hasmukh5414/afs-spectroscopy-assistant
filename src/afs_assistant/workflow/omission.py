"""Last-attempt omission of individually failing answer fields.

After the only repair, one unsupported paragraph or claim must not discard an
otherwise valid answer. Each failing field is removed whole (never rewritten or
given a different source), field citations are re-indexed, and an omission is
refused when it would leave a required field empty.
"""
import re

from afs_assistant.core.models import ChatAnswer

OPTIONAL = re.compile(r"^assessment\.candidates\[(\d+)\]\.(spectral_range|measurement_concept)$")
PART = re.compile(r"^parts\[(\d+)\]")
CLAIM = re.compile(r"^assessment\.candidates\[(\d+)\]\.claims\[(\d+)\]")
CANDIDATE_ITEM = re.compile(r"^assessment\.candidates\[(\d+)\]\.(limitations|assumptions)\[(\d+)\]")
CASE_ITEM = re.compile(r"^assessment\.(gaps|unknowns)\[(\d+)\]")
STUDY_ITEM = re.compile(r"^assessment\.experiment\.(steps|criteria_to_confirm)\[(\d+)\]")
# Fields that may never be emptied by omission.
REQUIRED_NONEMPTY = {"limitations", "steps", "criteria_to_confirm"}


def _remove(answer: ChatAnswer, container: str, items: list, index: int) -> None:
    """Delete items[index] and shift field citations that address later siblings."""
    del items[index]
    pattern = re.compile(rf"^{re.escape(container)}\[(\d+)\](.*)$")
    kept = []
    for citation in answer.citations:
        match = pattern.match(citation.field)
        if match and int(match[1]) == index:
            continue
        if match and int(match[1]) > index:
            citation.field = f"{container}[{int(match[1]) - 1}]{match[2]}"
        kept.append(citation)
    answer.citations = kept


def omit_failing_fields(answer: ChatAnswer, uncited_parts: list[int], issues: list[str]) -> list[str] | None:
    """Remove the failing fields in place and return their paths, or None if omission cannot fix them."""
    targets = [f"parts[{i}]" for i in uncited_parts]
    for issue in issues:
        path = issue.split(":", 1)[0].strip()
        if not any(p.match(path) for p in (OPTIONAL, PART, CLAIM, CANDIDATE_ITEM, CASE_ITEM, STUDY_ITEM)):
            return None  # e.g. the summary or a requirement cannot be omitted
        targets.append(path)
    case = answer.assessment
    omitted = []
    # Highest indexes first, so earlier sibling indexes stay valid.
    def order(path):
        return [int(n) for n in re.findall(r"\[(\d+)\]", path)]
    for path in sorted(dict.fromkeys(targets), key=order, reverse=True):
        indexes = order(path)
        if path.startswith("assessment.candidates[") and (case is None or indexes[0] >= len(case.candidates)):
            omitted.append(path)  # Its candidate was already removed with its last claim.
            continue
        if match := OPTIONAL.match(path):
            if case is None:
                return None
            setattr(case.candidates[int(match[1])], match[2], "")
            answer.citations = [c for c in answer.citations if c.field != path]
        elif match := PART.match(path):
            _remove(answer, "parts", answer.parts, int(match[1]))
        elif match := CLAIM.match(path):
            if case is None:
                return None
            index = int(match[1])
            candidate = case.candidates[index]
            _remove(answer, f"assessment.candidates[{index}].claims", candidate.claims, int(match[2]))
            if not candidate.claims:
                _remove(answer, "assessment.candidates", case.candidates, index)
        elif match := CANDIDATE_ITEM.match(path):
            if case is None:
                return None
            index, field, item = int(match[1]), match[2], int(match[3])
            values = getattr(case.candidates[index], field)
            if field in REQUIRED_NONEMPTY and len(values) <= 1:
                return None
            _remove(answer, f"assessment.candidates[{index}].{field}", values, item)
        elif match := CASE_ITEM.match(path):
            if case is None:
                return None
            _remove(answer, f"assessment.{match[1]}", getattr(case, match[1]), int(match[2]))
        elif match := STUDY_ITEM.match(path):
            if case is None or case.experiment is None:
                return None
            values = getattr(case.experiment, match[1])
            if len(values) <= 1:
                return None
            _remove(answer, f"assessment.experiment.{match[1]}", values, int(match[2]))
        omitted.append(path)
    # Something answerable and cited must remain.
    cited = any(p.evidence_ids for p in answer.parts) or bool(case and any(c.claims for c in case.candidates))
    if not answer.parts or all(p.kind == "question" for p in answer.parts) or not cited:
        return None
    return omitted
