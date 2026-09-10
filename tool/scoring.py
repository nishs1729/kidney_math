"""
scoring.py — Cheap, zero-dependency relevance signals for ranking a paper store.

These are *prescreen* signals, not a verdict: they exist so a reviewer (human
or agent) spends their reading budget on the most-likely-relevant papers
first, not to decide inclusion/exclusion on their own.

  heuristic_score(record, ...)      0-100, pure metadata: citation velocity,
                                     taxonomy tag-coverage, review boost.
  similarity_scores(question, recs) 0-1 per paper, TF-IDF cosine similarity
                                     between the question text and each
                                     paper's title+abstract. No embedding API
                                     — bag-of-words lexical overlap only, so it
                                     catches shared terminology but misses
                                     paraphrase/synonym matches an embedding
                                     model would catch.

Both are meant to be combined with a rubric-based read of the actual abstract
(see `relevance_score` in the paper store schema) rather than used alone.
"""

import math
import re
from datetime import date
from typing import Any, Dict, List

_STOPWORDS = {
    "the", "and", "for", "are", "with", "that", "this", "from", "have",
    "has", "been", "was", "were", "will", "can", "could", "would", "should",
    "which", "what", "how", "why", "when", "where", "who", "its", "their",
    "our", "these", "those", "into", "onto", "than", "then", "them", "they",
    "not", "but", "also", "such", "each", "both", "more", "most", "some",
    "any", "all", "may", "using", "used", "use", "based", "between", "within",
    "about", "over", "under", "per", "via", "one", "two", "role", "roles",
    "study", "studies", "paper", "article", "results", "data", "effect",
    "effects", "analysis", "model", "models",
}


def tokenize(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _term_frequencies(tokens: List[str]) -> Dict[str, float]:
    counts: Dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    n = len(tokens) or 1
    return {t: c / n for t, c in counts.items()}


def _build_idf(docs_tokens: List[List[str]]) -> Dict[str, float]:
    n_docs = len(docs_tokens)
    doc_freq: Dict[str, int] = {}
    for tokens in docs_tokens:
        for t in set(tokens):
            doc_freq[t] = doc_freq.get(t, 0) + 1
    return {t: math.log((n_docs + 1) / (d + 1)) + 1.0 for t, d in doc_freq.items()}


def _tfidf_vector(tokens: List[str], idf: Dict[str, float]) -> Dict[str, float]:
    tf = _term_frequencies(tokens)
    return {t: w * idf.get(t, 0.0) for t, w in tf.items()}


def _cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    dot = sum(v1[t] * v2[t] for t in common)
    n1 = math.sqrt(sum(w * w for w in v1.values()))
    n2 = math.sqrt(sum(w * w for w in v2.values()))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def similarity_scores(question: str, records: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    TF-IDF cosine similarity between `question` and each record's
    title+abstract, using the record set itself (plus the question) as the
    corpus for IDF weighting — so terms common across the whole batch (e.g.
    "kidney" in a kidney-focused store) are downweighted relative to terms
    that distinguish one paper from the rest.
    """
    texts = [f"{r.get('title', '')} {r.get('abstract', '')}" for r in records]
    doc_tokens = [tokenize(question)] + [tokenize(t) for t in texts]
    idf = _build_idf(doc_tokens)

    q_vec = _tfidf_vector(doc_tokens[0], idf)
    scores: Dict[str, float] = {}
    for record, tokens in zip(records, doc_tokens[1:]):
        v = _tfidf_vector(tokens, idf)
        scores[record["id"]] = round(_cosine(q_vec, v), 4)
    return scores


def _citations_per_year(record: Dict[str, Any]) -> float:
    try:
        year = int(record.get("year"))
    except (TypeError, ValueError):
        return 0.0
    age = max(date.today().year - year, 1)
    citation_count = record.get("citation_count") or 0
    return citation_count / age


def heuristic_score(record: Dict[str, Any], max_tags: int = 1) -> float:
    """
    0-100 pure-metadata relevance proxy, no abstract text involved:
      - citation velocity (citations/year, log-scaled so one blockbuster
        paper doesn't dominate the scale) — up to 60 points, saturating
        around 20 citations/year
      - taxonomy tag-coverage (how many distinct query_tags this paper
        matched, relative to the most-tagged paper in the batch) — up to 30
        points; a paper that keeps resurfacing under different taxonomy
        cells is more central to the question than one that showed up once
      - a flat +10 for review articles — undervalued by raw citation counts
        early on, but disproportionately useful for orientation
    """
    cpy = _citations_per_year(record)
    citation_component = min(math.log1p(cpy) / math.log1p(20), 1.0) * 60
    tag_component = min(len(record.get("query_tags", [])) / max(max_tags, 1), 1.0) * 30
    review_component = 10.0 if record.get("is_review") else 0.0
    return round(citation_component + tag_component + review_component, 2)
