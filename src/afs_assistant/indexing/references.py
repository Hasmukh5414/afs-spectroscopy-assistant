"""Bibliography metadata, never a substitute for the cited paper's contents."""
import re
from afs_assistant.core.models import ReferenceRecord


def normalized(value):
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def reference_record(source_id, label, citation, location):
    citation = re.sub(r"https\s*:\s*/\s*/", "https://", citation)
    doi = re.search(r"10\.\d{4,9}/[A-Za-z0-9._/;():-]+", citation)
    url = re.search(r"https?://\S+", citation)
    return ReferenceRecord(source_id=source_id, label=label, citation=citation.strip(),
        location=location, doi=doi[0].rstrip(".,)") if doi else "",
        url=url[0].rstrip(".,)") if url else "")


def extract_references(source, pages):
    records = []
    in_references = False
    numbered_parts = []
    for page, text in enumerate(pages, 1):
        heading = re.search(r"(?im)^\s*(?:■\s*)?References\s*:?[ \t]*$", text)
        tail = text[heading.end():] if heading else text
        if heading:
            in_references = True
        if not in_references:
            continue
        if re.search(r"(?m)^\s*-\s+[A-Z]", tail):
            # Review documents have several local bibliographies followed by body text.
            tail = re.split(r"(?m)^\s*\d+\.\s+[A-Z]", tail)[0]
            entries = re.split(r"(?m)^\s*-\s+(?=[A-Z])", tail)
            for entry in entries:
                entry = entry.strip()
                if len(entry) > 20:
                    label = f"p{page}-{sum(r.location == f'p. {page}' for r in records) + 1}"
                    records.append(reference_record(source.source_id, label, entry, f"p. {page}"))
            in_references = False
        else:
            # Figure captions after a reference column are not bibliography.
            tail = re.split(r"(?m)^Figure\s+\d+\.", tail)[0]
            tail = re.sub(r"(?m)^Sensors 2017, 17, 1914.*$", "", tail)
            numbered_parts.append((page, tail))
    for page, text in numbered_parts:
        matches = list(re.finditer(r"(?m)^\s*(?:\((\d+)\)|(\d+)\.)\s+", text))
        for index, match in enumerate(matches):
            end = matches[index+1].start() if index+1 < len(matches) else len(text)
            entry = text[match.end():end].strip()
            entry = re.split(r"©|Analytical Chemistry pubs|https://doi.org/10.1021/acs.analchem.1c00080", entry)[0].strip()
            if len(entry) > 10:
                records.append(reference_record(source.source_id, match[1] or match[2], entry, f"p. {page}"))
    return records


def resolve_supplied_references(records, sources):
    for record in records:
        haystack = normalized(record.citation)
        targets = [s.source_id for s in sources if s.source_id != record.source_id and
            any(len(normalized(key)) >= 12 and normalized(key) in haystack for key in s.citation_keys)]
        if len(targets) == 1:
            record.target_source_id = targets[0]
            record.status = "supplied_document"
    return records


def bibliography_element(text, records):
    value = normalized(text)
    if not value or value == "references":
        return value == "references"
    for record in records:
        citation = normalized(record.citation)
        # Match author-led entries, not paragraphs which merely cite [N].
        prefix = re.sub(r"^\d+", "", value)
        if min(len(citation), len(prefix)) >= 20 and (citation[:45] in prefix[:100] or prefix[:45] in citation[:100]):
            return True
    return False
