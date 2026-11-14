"""BM25 over discovered tool descriptions. No model call, no dependency."""

import math
import re
from collections import Counter

TOKEN = re.compile(r"[a-z0-9]+")
K1 = 1.5
B = 0.75


def tokens(text):
    return TOKEN.findall(text.lower())


def rank(task, tools):
    query = tokens(task)
    docs = [
        tokens(f"{tool['key'].replace('.', ' ')} {tool['key'].replace('.', ' ')} {tool['description']}")
        for tool in tools
    ]
    average = sum(len(doc) for doc in docs) / len(docs) if docs else 1.0
    document_frequency = Counter()
    for doc in docs:
        document_frequency.update(set(doc))

    scores = []
    for tool, doc in zip(tools, docs, strict=False):
        counts = Counter(doc)
        score = 0.0
        for term in query:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            total = len(docs)
            idf = math.log(1 + (total - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += idf * frequency * (K1 + 1) / (
                frequency + K1 * (1 - B + B * len(doc) / average)
            )
        scores.append((score, tool))
    scores.sort(key=lambda pair: -pair[0])
    return [tool for _, tool in scores]
