"""Conservative value/unit checks, not scientific claim verification.

Only explicit quantities in a claim's cited passages can support its numbers.
Compatible linear units may be converted; unrelated metadata, bare numbers and
small integers cannot authorize a specification. Matching still does not prove
that a source supports the same object, meaning or application.
"""
import json
import math
import re

from afs_assistant.core.models import ChatAnswer

# Only compatible units share a dimension. No arbitrary scaling, reciprocal
# wavelength conversions or tolerance for a different specification.
UNITS = {
    "nm": ("length", 1e-9), "µm": ("length", 1e-6), "μm": ("length", 1e-6),
    "um": ("length", 1e-6), "mm": ("length", 1e-3), "cm": ("length", .01),
    "m": ("length", 1), "km": ("length", 1000),
    "cm-1": ("wavenumber", 1), "cm⁻¹": ("wavenumber", 1), "cm−1": ("wavenumber", 1),
    "cm -1": ("wavenumber", 1), "cm - 1": ("wavenumber", 1), "cm^-1": ("wavenumber", 1), "cm-¹": ("wavenumber", 1),
    "ms": ("time", .001), "s": ("time", 1), "min": ("time", 60), "h": ("time", 3600),
    "mL": ("volume", .001), "L": ("volume", 1),
    "mg": ("mass", .001), "g": ("mass", 1),
    "mM": ("molarity", .001), "µM": ("molarity", 1e-6), "nM": ("molarity", 1e-9),
    "%": ("percent", 1), "°C": ("temperature", 1), "ppm": ("ppm", 1),
    "dB": ("decibels", 1), "g/L": ("mass concentration", 1),
    "samples": ("samples", 1), "specimens": ("specimens", 1),
}
NUMBER = r"[+-]?\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?"
# A single-letter unit must follow a space ("5 m", not the steel grade "316L").
SINGLE = [unit for unit in UNITS if len(unit) == 1 and unit.isalpha()]
UNIT = "|".join(re.escape(unit) for unit in sorted(set(UNITS) - set(SINGLE), key=len, reverse=True))
SINGLE_UNIT = "|".join(re.escape(unit) for unit in SINGLE)
# Spectral and geometric descriptors from cited passages may describe a proposed
# comparison; counts, concentrations, temperatures and limits must come from the customer.
DESCRIPTIVE = {"length", "wavenumber"}
# A number after these words labels a figure, table or study stage; it is not a setting.
LABEL = re.compile(r"(?:\bfig(?:ure)?|\btab(?:le)?|\bphase|\bstep|\bstage|\bslide|\bpage|\bsection|\bpart|"
                   r"\bround|\boption|\bcandidate|\btier|\blevel)\.?\s*$", re.IGNORECASE)
RANGE = re.compile(r"\d\s*(?:-|–|—|to)\s*[+-]?\d")
# Method families and sampling geometries named in the supplied material. A cited
# passage must concern the method/geometry its field describes.
FAMILIES = {"NIR": r"\bNIR\b|near[- ]infrared|near[- ]IR\b",
            "MIR": r"\bMIR\b|mid[- ]infrared|mid[- ]IR\b|\bATR\b|FTIR|\bIR spectroscopy",
            "Raman": r"raman", "fluorescence": r"fluoresc|fluorim",
            "UV-Vis": r"\bUV\b|UV[-/ ]?vis|ultraviolet|visible spectroscopy"}
GEOMETRIES = {"reflection": r"reflect|backscatter", "transmission": r"transmission|transflect",
              "ATR": r"\bATR\b", "cuvette": r"cuvette"}
QUANTITY = re.compile(rf"(?<![\w.])({NUMBER})(?:\s*(?:-|–|—|to)\s*({NUMBER}))?(?:\s*({UNIT})|\s+({SINGLE_UNIT}))(?![\w])")
# A single value next to these words is a source's hardware setting, not a range bound.
SETTING = re.compile(r"excitation|laser|\bLED\b|lamp|light source", re.IGNORECASE)


def quantities(text: str):
    for match in QUANTITY.finditer(text):
        dimension, factor = UNITS[match[3] or match[4]]
        for raw in match.group(1, 2):
            if raw is None:
                continue
            number = raw.replace(",", "") if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+", raw) else raw.replace(",", ".")
            yield float(number) * factor, dimension, match[0]


def source_text(record: dict) -> str:
    # A flattened chart cannot establish label/value associations. Only a
    # specific pixel observation may supply chart quantities in that case.
    fields = ("visual_observation",) if record.get("layout_warning") else (
        "text", "original_caption", "table_row", "notes", "surrounding_context", "visual_observation")
    return "\n".join(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                     for field in fields if (value := record.get(field)))


def verify(answer: ChatAnswer, user_text: str, inspected: dict) -> list[str]:
    """Flag missing value/unit pairs in all output fields; do not decide methods."""
    texts = {ref: source_text(record) for ref, record in inspected.items()}

    def passages(refs, spectral=False):
        # The workbook's physical sampling depth cannot authorize a wavelength
        # merely because both convert to the same length unit.
        return "\n".join(texts.get(ref, "") for ref in refs
            if not (spectral and isinstance(inspected.get(ref, {}).get("table_row"), dict)
                    and "optical_path_or_depth" in inspected[ref]["table_row"]))

    all_refs = {ref for part in answer.parts for ref in part.evidence_ids}
    case = answer.assessment
    if case:
        all_refs.update(ref for candidate in case.candidates for ref in candidate.evidence_ids)
        all_refs.update(ref for candidate in case.candidates for claim in candidate.claims for ref in claim.evidence_ids)
    cited_text = passages(all_refs)
    context = user_text + "\n" + cited_text
    proposal_fields = {f"parts[{i}].text" for i, part in enumerate(answer.parts) if part.kind == "proposal"}
    customer_context_fields = {f"parts[{i}].text" for i, part in enumerate(answer.parts)
                               if part.kind in {"question", "gap"}}
    fields = [(f"parts[{i}].text", part.text,
               passages(part.evidence_ids) if part.kind == "answer" else
               user_text if part.kind == "proposal" else context)
              for i, part in enumerate(answer.parts)]
    if case:
        fields.append(("assessment.summary", case.summary, context))
        fields.extend((f"assessment.{name}[{i}]", text, context)
                      for name in ("gaps", "unknowns") for i, text in enumerate(getattr(case, name)))
        fields.extend((f"assessment.requirements[{i}].statement", requirement.statement, requirement.user_quote)
                      for i, requirement in enumerate(case.requirements))
        fields.extend((f"assessment.questions[{i}].{name}", getattr(question, name), context)
                      for i, question in enumerate(case.questions) for name in ("question", "affects"))
        for i, candidate in enumerate(case.candidates):
            prefix = f"assessment.candidates[{i}]"
            refs = set(candidate.evidence_ids) | {ref for claim in candidate.claims for ref in claim.evidence_ids}
            evidence = passages(refs)
            fields.extend((f"{prefix}.{name}", getattr(candidate, name),
                           passages(candidate.evidence_ids, spectral=name == "spectral_range")
                           if name in {"measurement_concept", "spectral_range"} else evidence)
                          for name in ("name", "measurement_concept", "spectral_range"))
            fields.extend((f"{prefix}.claims[{j}].text", claim.text, passages(claim.evidence_ids))
                          for j, claim in enumerate(candidate.claims))
            fields.extend((f"{prefix}.{name}[{j}]", text, user_text + "\n" + evidence)
                          for name in ("assumptions", "limitations") for j, text in enumerate(getattr(candidate, name)))
        if case.experiment:
            # Published study settings do not prescribe a new customer's
            # test concentrations, sample counts or acceptance thresholds.
            fields.append(("assessment.experiment.objective", case.experiment.objective, user_text))
            fields.extend((f"assessment.experiment.{name}[{i}]", text, user_text)
                          for name in ("steps", "criteria_to_confirm")
                          for i, text in enumerate(getattr(case.experiment, name)))
    field_support = {citation.field: passages(citation.evidence_ids,
        spectral=citation.field.endswith(".spectral_range")) for citation in answer.citations}
    cited_values = list(quantities(cited_text))
    issues = []
    for field, text, evidence in fields:
        if field.startswith("assessment.experiment.") or field in proposal_fields:
            # Free-text modifiers must not hide numerical prescriptions from the
            # value/unit parser (e.g. a number followed by adjectives then samples).
            # Ignore list numbering and digits embedded in method names.
            study_text = re.sub(r"^\s*\d+[.)]\s+", "", text)
            # Inline enumerators such as "(1)" or "2)" order options; they are not settings.
            study_text = re.sub(r"\(\d{1,2}\)|(?<![\w.(])\d{1,2}\)(?=\s)", " ", study_text)
            spans = [match.span() for match in QUANTITY.finditer(study_text)]
            unparsed = [match[0] for match in re.finditer(rf"(?<![\w.]){NUMBER}(?!\w)", study_text)
                        if not any(start <= match.start() and match.end() <= end for start, end in spans)
                        and not LABEL.search(study_text[:match.start()])]
            if unparsed:
                issues.append(field + ": numerical study wording cannot be checked as explicit quantities: "
                    + ", ".join(sorted(set(unparsed))) + ". Remove numerical prescriptions or state "
                    "customer-supplied settings with an unambiguous unit/count noun. Do not invent counts or ratios.")
        if field in field_support and field not in proposal_fields and not field.startswith(("assessment.experiment.", "assessment.requirements[")):
            evidence = field_support[field]
            if field in customer_context_fields:
                # A disclaimer/question may repeat the customer's requested target.
                # Its citation must not turn that target into a source performance claim.
                evidence = user_text + "\n" + evidence
        allowed = list(quantities(evidence))
        study = field.startswith("assessment.experiment.") or field in proposal_fields
        unsupported = set()
        for value, dimension, display in quantities(text):
            supported = any(dimension == unit and math.isclose(value, source, rel_tol=1e-6, abs_tol=1e-15)
                            for source, unit, _ in allowed)
            # A study may describe a cited spectral/geometric range; a single source
            # setting (e.g. one excitation wavelength) is not the customer's configuration.
            if not supported and study and dimension in DESCRIPTIVE and RANGE.search(display):
                supported = any(dimension == unit and math.isclose(value, source, rel_tol=1e-6, abs_tol=1e-15)
                                for source, unit, _ in cited_values)
            if not supported:
                unsupported.add(display)
        if unsupported:
            if field.startswith("assessment.experiment.") or field in proposal_fields:
                issues.append(field + ": proposed study quantities were not supplied by the customer: "
                    + ", ".join(sorted(unsupported)) + ". Remove these numerical settings or targets and "
                    "ask the customer to agree them. Published example settings are not client requirements.")
            else:
                issues.append(field + ": quantities lack matching values and compatible units in this field's "
                              "supporting passages: " + ", ".join(sorted(unsupported))
                              + ". Correct this field's evidence_ids only if that passage supports its exact claim; "
                              "otherwise omit the numerical detail. Check structured fields as well as visible parts.")
    user_values = list(quantities(user_text))
    if case:
        for i, candidate in enumerate(case.candidates):
            concept = candidate.measurement_concept
            settings = sorted({match[0] for match in QUANTITY.finditer(concept)
                               if not match[2] and UNITS[match[3] or match[4]][0] in DESCRIPTIVE
                               and SETTING.search(concept[max(0, match.start() - 40):match.end() + 40])
                               and not any(math.isclose(value, source, rel_tol=1e-6) and dimension == unit
                                           for value, dimension, _ in quantities(match[0])
                                           for source, unit, _ in user_values)})
            if settings and "example" not in concept.lower():
                issues.append(f"assessment.candidates[{i}].measurement_concept: single source settings ("
                    + ", ".join(settings) + ") describe the cited study's hardware, not the customer's "
                    "configuration. Remove them or label them explicitly as a source example.")
    return issues + consistency_issues(answer, inspected)


def families(text: str) -> set[str]:
    return {name for name, pattern in FAMILIES.items() if re.search(pattern, text, re.IGNORECASE)}


def geometries(text: str) -> set[str]:
    return {name for name, pattern in GEOMETRIES.items() if re.search(pattern, text, re.IGNORECASE)}


def consistency_issues(answer: ChatAnswer, inspected: dict) -> list[str]:
    """A cited passage must concern the method family and table geometry its field describes."""
    def record_text(ref):
        record = inspected.get(ref, {})
        row = record.get("table_row")
        return json.dumps(row, ensure_ascii=False) if isinstance(row, dict) else source_text(record)

    checks = [(f"parts[{i}].text", part.text, part.evidence_ids)
              for i, part in enumerate(answer.parts) if part.kind == "answer"]
    case = answer.assessment
    if case:
        for i, candidate in enumerate(case.candidates):
            prefix = f"assessment.candidates[{i}]"
            for j, claim in enumerate(candidate.claims):
                # A claim that names no method inherits its candidate's methods.
                text = claim.text if families(claim.text) else candidate.name + ". " + claim.text
                checks.append((f"{prefix}.claims[{j}].text", text, claim.evidence_ids))
            concept_refs = list(candidate.evidence_ids) + [ref for c in answer.citations
                if c.field == f"{prefix}.measurement_concept" for ref in c.evidence_ids]
            if candidate.measurement_concept:
                checks.append((f"{prefix}.measurement_concept",
                               candidate.name + ". " + candidate.measurement_concept, concept_refs))
    issues = []
    for field, text, refs in checks:
        wanted, shapes = families(text), geometries(text)
        for ref in dict.fromkeys(refs):
            if ref not in inspected:
                continue
            passage = record_text(ref)
            found = families(passage)
            if wanted and found and not wanted & found:
                issues.append(f"{field}: cited {ref} concerns {', '.join(sorted(found))}, not "
                    f"{', '.join(sorted(wanted))}. Cite a passage about the method this field describes, "
                    "or remove the claim; a source about another method cannot support it.")
            row = inspected[ref].get("table_row")
            if isinstance(row, dict):
                technique = geometries(str(row.get("measurement_technique", "")) + " " + str(row.get("physical_phenomenon", "")))
                if "cuvette" in technique and "cuvette" not in shapes and not re.search(r"laborator|\blab\b", text, re.IGNORECASE):
                    issues.append(f"{field}: cited {ref} is a laboratory cuvette row, not a process probe or "
                        "interface. Cite the table row for the geometry this field describes.")
                elif shapes and technique and not shapes & technique:
                    issues.append(f"{field}: cited {ref} describes {', '.join(sorted(technique))} geometry, "
                        f"but the field describes {', '.join(sorted(shapes))}. Cite the matching row.")
    return issues
