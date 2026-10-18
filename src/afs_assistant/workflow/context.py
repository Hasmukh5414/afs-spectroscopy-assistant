"""Sentence-boundary clipping shared by evidence tools."""
import re


def clipped(text, limit=1800):
    if len(text) <= limit:
        return text, False
    prefix = text[:limit]
    boundaries = list(re.finditer(r"[.!?](?:\s|$)|\n", prefix))
    if boundaries and boundaries[-1].end() > limit // 2:
        prefix = prefix[:boundaries[-1].end()]
    return prefix.rstrip() + "…", True
