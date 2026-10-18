"""Rank fusion only; relevance scores never establish scientific support."""


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(dict.fromkeys(ranking), 1):
            scores[idx] = scores.get(idx, 0.0) + 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)
