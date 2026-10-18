"""Section/sentence chunking with source-aware atomic table rows."""
import re
from typing import Literal

from afs_assistant.config import Settings
from afs_assistant.core.fingerprints import fingerprint
from afs_assistant.core.models import (
    ChunkBatch, DocumentElement, EvidenceContext, EvidenceImage, EvidenceItem, ExtractedDocument, Locator, Provenance,
)

CHUNKER_VERSION = "afs-sections-v5"


def compact_table(text: str) -> str:
    """Remove Docling's column-alignment padding; cell boundaries are unchanged."""
    return "\n".join(re.sub(r" {2,}", " ", re.sub(r"-{4,}", "---", line)).rstrip()
                     for line in text.splitlines())


def indexes_nearby_text(source) -> bool:
    """Whole-slide images sit beside their flattened OCR, which is indexed separately."""
    return source.visual_strategy != "page"


def unique_provenance(elements: list[DocumentElement]) -> list[Provenance]:
    result = []
    seen = set()
    for element in elements:
        for location in element.provenance:
            key = (location.page, location.bbox, location.coordinate_origin)
            if key not in seen:
                seen.add(key)
                result.append(location)
    return result


class DocumentChunker:
    def __init__(self, settings: Settings):
        # A deterministic sentencizer needs no downloaded language-model weights.
        import spacy
        self.nlp = spacy.blank("xx")
        self.nlp.add_pipe("sentencizer")
        self.settings = settings
        self.cache_identity = CHUNKER_VERSION

    def count(self, text: str) -> int:
        return len(self.nlp.make_doc(text))

    def _sentences(self, text: str) -> list[str]:
        return [sentence.text.strip() for sentence in self.nlp(text).sents if sentence.text.strip()]

    def process_material(self, document: ExtractedDocument) -> ChunkBatch:
        sections: list[tuple[list[str], list[DocumentElement]]] = []
        heading_path: list[str] = []
        current: list[DocumentElement] = []
        for element in document.elements:
            if element.label in {"page_header", "page_footer"}:
                continue
            if self.settings.chunking_strategy == "section_aware" and element.label in {"title", "section_header"}:
                if current:
                    sections.append((list(heading_path), current))
                level = max(1, element.heading_level or 1)
                heading_path = heading_path[:level - 1] + [element.text.strip()]
                current = [element]
            else:
                current.append(element)
        if current:
            sections.append((list(heading_path), current))

        items: list[EvidenceItem] = []
        contexts: list[EvidenceContext] = []
        source = document.source
        for headings, elements in sections:
            parent_text = "\n\n".join((element.text or "[Uncaptioned image; pixels not interpreted]") + ("\n" + element.notes if element.notes else "")
                                      for element in elements if element.text.strip() or element.label == "picture")
            if not parent_text:
                continue
            parent_id = source.source_id[:40] + "_ctx_" + fingerprint([source.sha256, [e.element_id for e in elements]])[:32]
            contexts.append(EvidenceContext(context_id=parent_id, text=parent_text, provenance=unique_provenance(elements)))
            pending_text: list[str] = []
            pending_elements: list[DocumentElement] = []

            def emit(text: str, refs: list[DocumentElement], element_type: Literal["text", "table_row", "figure_caption", "image"] = "text", image: EvidenceImage | None = None):
                locations = unique_provenance(refs)
                if not locations:
                    raise ValueError("Searchable evidence requires a physical page location")
                pages = sorted({location.page for location in locations})
                label = f"PDF p. {pages[0]}" if len(pages) == 1 else f"PDF pp. {pages[0]}–{pages[-1]}"
                identity = fingerprint([source.sha256, CHUNKER_VERSION, self.settings.chunk_size,
                    self.settings.chunking_strategy, [ref.element_id for ref in refs], text])
                qualifications = []
                if source.authority != "provided":
                    qualifications.append("Automatically extracted source content; exact equipment values require reviewed assertions.")
                if source.issue_ids:
                    qualifications.append("Unresolved source review tasks: " + ", ".join(source.issue_ids))
                if image is not None:
                    qualifications.append("Image crop retained separately. Search uses its caption and nearby text; pixels, axes and plotted values have not been interpreted. Image role is unclassified and may be decorative.")
                items.append(EvidenceItem(evidence_id=source.source_id[:40] + "_" + identity[:40],
                    source_id=source.source_id, source_version=source.sha256, title=source.title,
                    text=text, locator=Locator(kind="page", label=label), kind=source.kind,
                    review_status="extracted", synthetic=False, qualifications=qualifications,
                    element_ids=list(dict.fromkeys(ref.element_id for ref in refs)), provenance=locations,
                    heading_path=headings, parent_context_id=parent_id, element_type=element_type,
                    issue_ids=source.issue_ids, oversized=self.count(text) > self.settings.chunk_size, image=image))

            def flush():
                if pending_text:
                    emit("\n".join(pending_text), list(pending_elements))
                    pending_text.clear()
                    pending_elements.clear()

            for index, element in enumerate(elements):
                if element.label == "picture":
                    flush()
                    if not element.image:
                        raise ValueError("A parsed picture is missing its retained crop")
                    neighbors = [elements[i].text.strip() for i in (index-1, index+1)
                                 if 0 <= i < len(elements) and elements[i].label not in {"picture", "table"} and elements[i].text.strip()]
                    nearby = "\n".join(headings + neighbors)[:1200]
                    image = EvidenceImage(filename=element.image, caption=element.caption or element.text, nearby_text=nearby)
                    text = f"Image from {source.title}.\nCaption: {image.caption or '[not supplied]'}"
                    if nearby and indexes_nearby_text(source):
                        text += "\nNearby document text (context, not image interpretation): " + nearby
                    text += "\nVisual content has not been interpreted."
                    emit(text, [element], "image", image)
                    continue
                if not element.text.strip():
                    continue
                if element.label == "table":
                    flush()
                    lines = compact_table(element.text.strip()).splitlines()
                    header_end = next((i for i, line in enumerate(lines)
                        if re.match(r"^\s*\|?\s*:?-{3,}", line)), None)
                    if header_end is None:
                        # An unresolved table stays intact instead of inventing cell boundaries.
                        emit(compact_table(element.text) + ("\n" + element.notes if element.notes else ""), [element], "table_row")
                    else:
                        header = "\n".join(lines[:header_end + 1])
                        for row in lines[header_end + 1:]:
                            if row.strip():
                                emit(header + "\n" + row + ("\n" + element.notes if element.notes else ""), [element], "table_row")
                    continue
                for sentence in self._sentences(element.text):
                    if pending_text and self.count("\n".join(pending_text + [sentence])) > self.settings.chunk_size:
                        flush()
                    # Prose is bounded without silently dropping a long sentence.
                    tokens = list(self.nlp.make_doc(sentence))
                    if len(tokens) > self.settings.chunk_size:
                        flush()
                        for start in range(0, len(tokens), self.settings.chunk_size):
                            group = tokens[start:start + self.settings.chunk_size]
                            emit(sentence[group[0].idx:group[-1].idx + len(group[-1].text)].strip(), [element])
                    else:
                        pending_text.append(sentence)
                        pending_elements.append(element)
            flush()
        if not items:
            raise ValueError("Document produced no located evidence chunks")
        return ChunkBatch(items=items, contexts=contexts)
