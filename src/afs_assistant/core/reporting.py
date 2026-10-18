"""Readable per-assessment exports from the same data rendered in chat."""
import re

STOPWORDS = {"a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "is", "are", "be", "may",
             "can", "this", "that", "it", "its", "as", "by", "at", "from", "not", "if", "whether", "e", "g"}


def content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOPWORDS}


def distinct(texts, seen: list[set[str]]):
    """Drop exact and near repeats: a line whose content words are already covered by an earlier line."""
    kept = []
    for text in texts:
        words = content_words(text)
        repeat = any(words == earlier or (min(len(words), len(earlier)) >= 3
                     and len(words & earlier) / min(len(words), len(earlier)) >= 0.8) for earlier in seen)
        if words and not repeat:
            seen.append(words)
            kept.append(text)
    return kept


def assessment_markdown(payload: dict) -> str:
    answer, sources = payload["answer"], payload["sources"]
    lines = ["# AFS technical assessment", "", payload["question"], "",
             f"Evidence release: {payload['release']}", ""]
    if answer.get("citation_status"):
        lines += [f"Source citations: {answer['citation_status']} (not scientific verification)", ""]

    source_numbers = {ref: index for index, ref in enumerate(sources, 1)}
    def citations(refs=(), field="", shown=frozenset()):
        precise = [ref for citation in answer.get("citations", [])
            if citation["field"] == field for ref in citation["evidence_ids"]]
        linked = precise or list(refs)
        numbers = [str(source_numbers[ref]) for ref in dict.fromkeys(linked)
                   if ref in sources and str(source_numbers[ref]) not in shown]
        return " [" + ", ".join(numbers) + "]" if numbers else ""

    def section(title, values):
        if values:
            lines.extend(["## " + title, ""])
            lines.extend("- " + text for text in dict.fromkeys(values))
            lines.append("")

    case = answer.get("assessment")
    risk_words: list[set[str]] = [content_words(part["text"]) for part in answer["parts"] if part["kind"] == "gap"]
    if case:
        status = {"ready_for_review": "Provisional feasibility assessment",
                  "awaiting_clarification": "Further clarification needed",
                  "incomplete": "Insufficient supporting evidence"}
        lines += ["Decision status: " + status[case["status"]], ""]
    lines += ["## Answer", ""]
    for index, part in enumerate(answer["parts"]):
        # Inline [n] markers already show their sources beside each sentence.
        inline = frozenset(re.findall(r"\[(\d+)\]", part["text"]))
        lines += [part["text"] + citations(part.get("evidence_ids", []), f"parts[{index}].text", inline), ""]
    if case:
        lines += ["## Full assessment", ""]
        if not any(part["kind"] == "answer" for part in answer["parts"]):
            lines += [case["summary"], ""]
        section("Process requirements and constraints",
                [item["statement"] + '\n  Customer: "' + item["user_quote"] + '"' for item in case.get("requirements", [])])
        if case.get("candidates"):
            lines += ["## Approaches worth testing", ""]
        for index, candidate in enumerate(case.get("candidates", [])):
            path = f"assessment.candidates[{index}]"
            lines += ["### " + candidate["name"], ""]
            for claim_index, claim in enumerate(candidate["claims"]):
                lines += [claim["text"] + citations(claim["evidence_ids"], f"{path}.claims[{claim_index}].text"), ""]
            if candidate.get("measurement_concept"):
                lines += ["**Measurement / probe concept:** " + candidate["measurement_concept"]
                          + citations(candidate.get("evidence_ids", []), path+".measurement_concept"), ""]
            if candidate.get("spectral_range"):
                lines += ["**Spectral range:** " + candidate["spectral_range"]
                          + citations(candidate.get("evidence_ids", []), path+".spectral_range"), ""]
            for field, title in (("limitations", "Limitations"), ("assumptions", "Assumptions to confirm")):
                items = distinct([text + citations(field=f"{path}.{field}[{n}]")
                                  for n, text in enumerate(candidate.get(field, []))], risk_words)
                if items:
                    lines += [f"**{title}:**", "", *("- " + text for text in items), ""]
        section("Important clarification questions",
                [question["question"] + " — " + question["affects"] for question in case.get("questions", [])])
        section("Shared risks and evidence gaps", distinct(
            [text + citations(field=f"assessment.gaps[{index}]") for index, text in enumerate(case.get("gaps", []))], risk_words))
        experiment = case.get("experiment")
        if experiment:
            lines += ["## First feasibility study", "", experiment["objective"] + citations(field="assessment.experiment.objective"), ""]
            lines += [f"{index}. {step}" + citations(field=f"assessment.experiment.steps[{index-1}]")
                      for index, step in enumerate(experiment["steps"], 1)]
            lines.append("")
            section("Criteria to agree and check", [text + citations(field=f"assessment.experiment.criteria_to_confirm[{index}]")
                for index, text in enumerate(experiment["criteria_to_confirm"])])
    if sources:
        lines += ["## Sources", ""]
        for ref, source in sources.items():
            label = source.get("citation_label") or f"{source['source_id']} {source['locator']['label']}"
            lines.append(f"{source_numbers[ref]}. {label} — {source['title']}")
            lines += ["", "   " + source.get("text", "")[:220].replace("\n", " ") + "…", ""]
    return "\n".join(lines).rstrip() + "\n"
