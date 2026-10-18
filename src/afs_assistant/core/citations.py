"""Explicit field-level references; no source guessing from text similarity."""
import re
from typing import Any, Iterator

from .models import ChatAnswer, FieldCitation

MARKER = re.compile(r"\s*(?:\[E\d+(?:\s*,\s*E\d+)*\]|\(E\d+(?:\s*,\s*E\d+)*\))")
PARAGRAPH = re.compile(r"^parts\[\d+\]\.text$")
DISPLAY = re.compile(r"\[(E\d+)\]")
SKIP = {"citations", "evidence_ids", "requirements", "kind", "intent", "status",
        "schema_version", "citation_status", "evidence_coverage", "field"}


def prose_fields(value: Any, path: str = "") -> Iterator[tuple[str, Any, Any]]:
    """Yield mutable containers and paths for generated prose, excluding user quotes."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in SKIP:
                continue
            location = f"{path}.{key}" if path else key
            if isinstance(child, str):
                yield location, value, key
            else:
                yield from prose_fields(child, location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            location = f"{path}[{index}]"
            if isinstance(child, str):
                yield location, value, index
            else:
                yield from prose_fields(child, location)


def object_at(payload: Any, path: str) -> Any:
    """The value at a field path such as assessment.candidates[0], or None if it does not exist."""
    value = payload
    for key, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path):
        if index and isinstance(value, list) and int(index) < len(value):
            value = value[int(index)]
        elif key and isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value


def normalize_citations(answer: ChatAnswer) -> ChatAnswer:
    """Preserve declared inline references as metadata before removing their markers."""
    payload = answer.model_dump()
    field_refs = {}
    fields = {path for path, _, _ in prose_fields(payload)}
    for citation in answer.citations:
        path = citation.field
        owner = object_at(payload, path.removesuffix(".evidence_ids")) if path.endswith(".evidence_ids") else None
        if isinstance(owner, dict) and isinstance(owner.get("evidence_ids"), list):
            # Citing an object's own reference slot declares exactly what that slot holds.
            owner["evidence_ids"] = list(dict.fromkeys([*owner["evidence_ids"], *citation.evidence_ids]))
            continue
        # A claim/part object has one explicit text leaf. Accept that structural
        # shorthand without guessing a source, another index or customer quote.
        if path not in fields and path + ".text" in fields:
            path += ".text"
        field_refs.setdefault(path, []).extend(citation.evidence_ids)
    for path, parent, key in prose_fields(payload):
        text = parent[key]
        if key == "text" and isinstance(parent, dict) and "evidence_ids" in parent and path in field_refs:
            # A paragraph or claim has its own reference slot; a field citation of
            # its text declares the same references, so keep one representation.
            parent["evidence_ids"] = list(dict.fromkeys([*parent["evidence_ids"], *field_refs.pop(path)]))
        refs = [ref for group in MARKER.findall(text) for ref in re.findall(r"E\d+", group)]
        if not refs:
            continue
        if PARAGRAPH.match(path):
            # Visible paragraphs keep their markers in place (one canonical [E#] each)
            # so every sentence shows its own source; references follow first use.
            parent[key] = MARKER.sub(lambda m: " " + "".join(f"[{ref}]" for ref in re.findall(r"E\d+", m[0])), text).strip()
            parent["evidence_ids"] = list(dict.fromkeys([*refs, *parent["evidence_ids"]]))
            continue
        parent[key] = MARKER.sub("", text).strip()
        if isinstance(parent, dict) and (key == "text" or key in {"measurement_concept", "spectral_range"}) and "evidence_ids" in parent:
            parent["evidence_ids"] = list(dict.fromkeys([*parent["evidence_ids"], *refs]))
        else:
            field_refs.setdefault(path, []).extend(refs)
    payload["citations"] = [FieldCitation(field=path, evidence_ids=list(dict.fromkeys(refs))).model_dump()
                            for path, refs in field_refs.items()]
    return ChatAnswer.model_validate(payload)


def strip_markers(text: str) -> str:
    return MARKER.sub("", text).strip()


def number_markers(answer: ChatAnswer, aliases: dict, order: list[str]) -> None:
    """Rewrite each paragraph's [E#] marker in place as its 1-based number in the sources list."""
    number = {eid: index for index, eid in enumerate(order, 1)}
    for part in answer.parts:
        part.text = DISPLAY.sub(lambda m: f"[{number[aliases[m[1]]]}]" if aliases.get(m[1]) in number else "", part.text)
