"""Visual search and source-display policies."""
import re


def visual_search_text(text: str) -> tuple[str, bool]:
    """Keep axes/legends, omit numerical curve readings inferred by vision."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n\n", text)
    reading = re.compile(r"\b(?:peaks?|bands?|curves?)\b[^.\n]{0,160}\b(?:between|near|around|approximately|about|at)\s+(?:[xy]\s*=\s*)?\d", re.I)
    kept = [part for part in parts if not reading.search(part)]
    return "\n\n".join(kept).strip(), len(kept) != len(parts)
def decorative_image(item) -> bool:
    """Exclude explicitly described branding without changing published records.

    Use the first descriptive sentence, not nearby scientific text. A plot that
    merely includes a logo remains eligible. Unclassified images stay searchable.
    """
    image = item.image
    if image is None or image.description is None:
        return False
    opening = re.split(r"[.!?]\s", image.description.text, maxsplit=1)[0][:300]
    branding = re.search(r"\b(?:logo|brand(?:ing)? banner|company emblem)\b", opening, re.IGNORECASE)
    scientific = re.search(r"\b(?:plot|graph|spectr\w*|diagram|probe|chart|table)\b", opening, re.IGNORECASE)
    return branding is not None and scientific is None


def caption_figure(item, items):
    """Resolve a standalone caption to one exact same-version image caption.

    Whitespace/punctuation formatting may differ after PDF extraction. Never
    associate images by page proximity or figure number alone.
    """
    def normalized(text):
        return re.sub(r"[\W_]+", "", text.casefold())
    if item.get("image") or not re.match(r"^\s*(?:fig\.?|figure)\s*\d+\b", item["text"], re.I):
        return None
    caption = normalized(item["text"])
    matches = [candidate for candidate in items
        if candidate.image and candidate.review_status != "disputed"
        and candidate.source_id == item["source_id"]
        and candidate.source_version == item["source_version"]
        and normalized(candidate.image.caption) == caption]
    return matches[0] if len(matches) == 1 else None
