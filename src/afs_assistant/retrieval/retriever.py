import json
import math
import re
from afs_assistant.config import Settings
from afs_assistant.core.lexical_index import build_index, tokens
from afs_assistant.core.models import Corpus, SearchRequest
from afs_assistant.core.embeddings import validate_vectors
from afs_assistant.core.source_policy import decorative_image
from .fusion import reciprocal_rank_fusion


def cosine(a, b):
    if len(a) != len(b):
        raise ValueError("Query and corpus embedding dimensions differ")
    denominator = math.sqrt(sum(x*x for x in a) * sum(y*y for y in b))
    return sum(x*y for x, y in zip(a, b)) / denominator if denominator else 0


class Retrieval:
    def __init__(self, snapshot, gateway=None, settings=None):
        self.settings = settings or Settings()
        self.corpus = Corpus.model_validate_json(snapshot["corpus"])
        self.vectors = json.loads(snapshot["vectors"])
        self.model = snapshot["model"]
        self.gateway = gateway
        self.by_id = {item.evidence_id: item for item in self.corpus.items}
        self.source_by_id = {source.source_id: source for source in self.corpus.sources}
        self.contexts = {item.context_id: item for item in self.corpus.contexts}
        self.items = [item for item in self.corpus.items if item.review_status != "disputed" and not decorative_image(item)]
        self.index = build_index(tuple(item.text for item in self.items)) if self.items else None
        self.vector_by_id = dict(zip([i.evidence_id for i in self.corpus.items], self.vectors))

    def similarity(self, query, items, timeout=20):
        """Cosine similarity of each item's stored embedding to the query, or None without embeddings.

        Costs one query embedding; passage vectors were computed at indexing time.
        """
        if not self.gateway or not items or self.model != self.gateway.settings.embedding_model or not self.vectors:
            return None
        vector = self.gateway.embed([query], timeout)[0]
        validate_vectors([vector], 1)
        return [cosine(vector, self.vector_by_id[item.evidence_id]) for item in items]

    def search(self, request: SearchRequest, timeout=20):
        items = [item for item in self.items
            if (not request.source_ids or item.source_id in request.source_ids)
            and (not request.element_types or item.element_type in request.element_types)
            and (not request.roles or (self.source_by_id.get(item.source_id) and self.source_by_id[item.source_id].evidence_role in request.roles))]
        if not items:
            return {"mode": "lexical", "items": [], "gaps": ["Evidence release is empty"]}
        index = self.index if items == self.items else build_index(tuple(item.text for item in items))
        if index is None:
            return {"mode": "lexical", "items": [], "gaps": ["No searchable evidence"]}
        lexical = index.search(request.query, self.settings.candidate_limit)
        lexical_scores = list(index.index.get_scores(tokens(request.query))) if index.index is not None else [0.0] * len(items)
        similarity = [None] * len(items)
        rankings = [lexical]
        mode = "lexical"
        if self.gateway:
            if self.model != self.gateway.settings.embedding_model or not self.vectors:
                raise ValueError("Re-ingest evidence with the configured embedding model before hybrid search")
            query = self.gateway.embed([request.query], timeout)[0]
            validate_vectors([query], 1)
            similarity = [cosine(query, self.vector_by_id[item.evidence_id]) for item in items]
            semantic = [i for i in sorted(range(len(similarity)), key=lambda i: similarity[i], reverse=True)
                        if similarity[i] >= self.settings.min_similarity][:self.settings.candidate_limit]
            rankings.append(semantic)
            mode = "hybrid"
        fused = reciprocal_rank_fusion(rankings, self.settings.rrf_k)
        return {"mode": mode, "score_kind": "rrf", "items": [
            {"evidence": items[idx].model_dump(), "rank": rank, "score": score,
             "cosine_similarity": similarity[idx], "bm25_score": float(lexical_scores[idx]),
             "routes": [name for name, ranking in zip(["lexical", "semantic"], rankings) if idx in ranking]}
            for rank, (idx, score) in enumerate(fused[:request.limit], 1)], "gaps": [] if fused else ["No matching admitted evidence"]}

    def context(self, evidence_id):
        item = self.by_id.get(evidence_id)
        if item is None:
            raise ValueError("Evidence ID does not exist in this release")
        result = item.model_dump()
        parent = self.contexts.get(item.parent_context_id or "")
        if parent:
            result["parent_text"] = parent.text[:20000]
            result["parent_truncated"] = len(parent.text) > 20000
        return result

    def figures(self, source_reference: str, number: int):
        """Resolve a named source figure directly to its independently indexed crop."""
        def normalize(value):
            return re.sub(r"([A-Z]+)0+(\d)", r"\1\2", value.upper())
        target = normalize(source_reference)
        found = []
        for item in self.items:
            if item.element_type != "image" or item.image is None:
                continue
            aliases = [item.source_id] + re.findall(r"\bAN\s*\d+\b", item.title, re.IGNORECASE)
            if target not in {normalize(alias.replace(" ", "")) for alias in aliases}:
                continue
            caption = re.match(r"\s*(?:figure|fig\.?)\s*(\d+)\b", item.image.caption, re.IGNORECASE)
            if caption and int(caption[1]) == number:
                found.append(item)
        return found

