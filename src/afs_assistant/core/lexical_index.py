"""Shared lexical indexing; retain technical symbols and avoid executable caches."""
from functools import lru_cache
from collections import Counter
import math
import re

from rank_bm25 import BM25Okapi


# Query and document tokenization must agree. Preserve negation, units, single
# letter scientific labels and product codes; remove ordinary English glue words.
STOP_WORDS = frozenset("""a an the is are was were be been being am i me my we us our
you your he him his she her it its they them their this that these those
to of in on at for from by with as and or but if then than so because
what which who whom whose why how when where do does did doing have has had
can could would should will shall may might must please tell give need want
some any each every very also just about into through during before after
there here such much many more most less least other another own same
""".split())


def tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"\w+(?:[-.+]\w+)*", text.casefold()) if token not in STOP_WORDS]


class BM25Index:
    def __init__(self, texts: tuple[str, ...]):
        tokenized = [tokens(text) for text in texts]
        self.index = BM25Okapi(tokenized, epsilon=.5) if any(tokenized) else None
        if self.index is not None:
            # Positive Robertson IDF preserves valid matches in a one-document corpus.
            # An absent query token still contributes zero.
            frequency = Counter(token for document in tokenized for token in set(document))
            total = len(tokenized)
            self.index.idf = {token: math.log(1 + (total - count + .5) / (count + .5))
                              for token, count in frequency.items()}

    def search(self, query: str, limit: int) -> list[int]:
        if self.index is None:
            return []
        scores = self.index.get_scores(tokens(query))
        return [i for i in sorted(range(len(scores)), key=lambda i: float(scores[i]), reverse=True)
                if scores[i] > 0][:limit]


@lru_cache(maxsize=4)
def build_index(texts: tuple[str, ...]) -> BM25Index:
    """One index build per immutable corpus, shared across investigations."""
    return BM25Index(texts)
