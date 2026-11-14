"""Small deterministic BM25 matcher; descriptions are data, never instructions."""

import math
import re
from collections import Counter

STOP = {"a", "an", "and", "the", "to", "for", "on", "in", "of", "about", "with", "by", "from"}


def tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [word[:-1] if word.endswith("s") and len(word) > 3 else word for word in words if word not in STOP]


class Matcher:
    def __init__(self, catalog):
        self.catalog = list(catalog.values()) if isinstance(catalog, dict) else list(catalog)
        # Capability labels do not improve natural-language goal matching.
        self.documents = [tokens(tool["name"].replace("_", " ") + " " + tool["description"].split("[consumes:")[0]) for tool in self.catalog]
        self.counts = [Counter(document) for document in self.documents]
        self.length = sum(map(len, self.documents)) / max(1, len(self.documents))
        self.frequency = Counter(term for document in self.documents for term in set(document))

    def rank(self, task, allowed=None, limit=10):
        terms = set(tokens(task))
        allowed = set(allowed) if allowed is not None else None
        ranked = []
        for tool, document, count in zip(self.catalog, self.documents, self.counts, strict=True):
            if allowed is not None and tool["key"] not in allowed:
                continue
            score = 0.0
            for term in terms:
                frequency = count[term]
                if frequency:
                    inverse = math.log(1 + (len(self.catalog) - self.frequency[term] + 0.5) / (self.frequency[term] + 0.5))
                    score += inverse * frequency * 2.2 / (frequency + 1.2 * (0.25 + 0.75 * len(document) / max(self.length, 1)))
            if score > 0:
                ranked.append({"key": tool["key"], "score": score})
        ranked.sort(key=lambda item: (-item["score"], item["key"]))
        return ranked[:limit]

    def match(self, task, allowed=None):
        ranked = self.rank(task, allowed=allowed, limit=10)
        if not ranked:
            return {"goal": None, "candidates": []}
        return {"goal": ranked[0]["key"], "candidates": [item["key"] for item in ranked]}
