"""
query_utils.py — Query construction & linting helpers for the search loop.

Two things this session's manual query-writing got wrong, silently:
  1. `("tubuloglomerular feedback" OR "macula densa" signal)` — the trailing
     `signal` after the closing quote has no AND/OR/NOT before it. PubMed's
     parser tolerated the implicit adjacency; Europe PMC's fulltext search did
     not, and matched "signal" as its own free-floating term against ~28k
     preprints.
  2. A bare abbreviation (`TGF`) that is heavily overloaded in the literature
     (mostly means "transforming growth factor beta", not the intended
     "tubuloglomerular feedback") pulled in unrelated fibrosis/cancer papers
     once queried against a large full-text index.

`lint_query()` catches both classes mechanically, before any query reaches an
API — for whatever topic the query was written for, not just this session's.
`build_queries()` mechanically assembles a taxonomy grid of queries from a
vocabulary table instead of hand-typing each one, which is what let bug (1)
slip through unnoticed in the first place.
"""

import re
from typing import Dict, List, Optional

# Abbreviations/operators that are safe to leave bare (query-syntax keywords,
# or short field-tag-ish tokens that would otherwise trip the ambiguity check).
_SAFE_BARE_TOKENS = {
    "AND", "OR", "NOT",
    "DNA", "RNA", "PCR", "MRI", "CT", "PET",
    "PDF", "DOI", "PMC", "PMID", "ID", "URL",
}


def lint_query(query: str) -> List[str]:
    """
    Return a list of human-readable warnings for a query string, or [] if clean.

    Checks (all topic-agnostic):
      - balanced quotes
      - balanced parentheses (ignoring parens inside quoted phrases)
      - a quoted phrase immediately followed by a bare word with no explicit
        AND/OR/NOT between them (implicit-adjacency bug)
      - bare (unquoted) short all-caps tokens not in a known-safe list, which
        are usually ambiguous abbreviations in biomedical/scientific text
    """
    warnings: List[str] = []

    if query.count('"') % 2 != 0:
        warnings.append("Unbalanced quotes (odd number of \").")

    depth = 0
    in_quote = False
    for ch in query:
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    warnings.append("Unbalanced parentheses: an extra ')' with no matching '('.")
                    depth = 0
    if depth > 0:
        warnings.append("Unbalanced parentheses: missing ')' to close an open '('.")

    for m in re.finditer(r'"[^"]*"\s+(?!(?:AND|OR|NOT)\b)([A-Za-z][\w-]*)', query):
        warnings.append(
            f"Quoted phrase is directly followed by the bare word {m.group(1)!r} with no "
            "AND/OR/NOT between them. Either fold it into the phrase "
            f'(e.g. "... {m.group(1)}") or add an explicit operator — different search '
            "backends resolve this ambiguity differently."
        )

    stripped = re.sub(r'"[^"]*"', " ", query)
    for m in re.finditer(r"\b([A-Z]{2,6})\b(?!:)", stripped):
        tok = m.group(1)
        if tok in _SAFE_BARE_TOKENS:
            continue
        warnings.append(
            f"Bare abbreviation {tok!r} outside quotes — short acronyms are often overloaded "
            "across the literature (they may mean several unrelated things). Consider spelling "
            "it out, or requiring it co-occur with a disambiguating term."
        )

    return warnings


def build_queries(
    rows: List[Dict[str, object]],
    columns: Dict[str, str],
    extra_and: Optional[str] = None,
) -> Dict[str, str]:
    """
    Mechanically build a taxonomy grid of queries (rows x columns) instead of
    hand-typing each one.

    Args:
        rows: list of {"tag": str, "or_terms": List[str]} — `or_terms` must
            already be fully quoted/parenthesized as needed (e.g. from a
            vocabulary table built by `expand_concepts`); they are OR'd
            together to form the row's subject clause.
        columns: {column_tag: modifier_clause} — a fully-formed boolean
            fragment per column (e.g. '("mathematical model" OR "computational
            model")'), applied to every row.
        extra_and: optional clause ANDed onto every query (e.g. a
            domain-scoping term like `kidney`), applied uniformly regardless
            of topic.

    Returns:
        {query_string: "{row_tag}:{column_tag}"} — ready to hand to
        `run_batch(queries=..., query_tags=...)`. Every generated query is
        passed through `lint_query`; if any warnings are raised, a ValueError
        listing them is thrown instead of returning a query batch, since a
        query with an implicit-adjacency or bare-abbreviation problem earned
        from a *manually authored* `or_terms`/`columns` list this session's
        exact bugs would otherwise reappear.
    """
    queries: Dict[str, str] = {}
    lint_errors: List[str] = []

    for row in rows:
        row_tag = row["tag"]
        or_terms = row["or_terms"]
        row_clause = "(" + " OR ".join(or_terms) + ")"
        for col_tag, col_clause in columns.items():
            parts = [row_clause, col_clause]
            if extra_and:
                parts.append(extra_and)
            q = " AND ".join(parts)
            tag = f"{row_tag}:{col_tag}"

            problems = lint_query(q)
            if problems:
                lint_errors.append(f"[{tag}] {q!r}:\n  - " + "\n  - ".join(problems))
            queries[q] = tag

    if lint_errors:
        raise ValueError(
            "build_queries: the following generated queries failed linting — fix the "
            "row/column inputs before running them:\n\n" + "\n\n".join(lint_errors)
        )

    return queries


def review_clause(source: str) -> str:
    """
    Return the publication-type-restriction fragment for 'review' articles,
    in the syntax the given source actually understands.

    PubMed and Europe PMC (bioRxiv/medRxiv) both index publication type;
    OpenAlex does not expose it precisely enough to filter on reliably (see
    `_norm_openalex` in search_utils.py) — don't request a review clause for it.
    """
    if source == "pubmed":
        return '"Review"[Publication Type]'
    if source in ("biorxiv", "medrxiv", "europepmc"):
        return 'PUB_TYPE:"Review"'
    raise ValueError(f"No review clause defined for source {source!r} (try 'pubmed' or 'biorxiv'/'medrxiv').")
