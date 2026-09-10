# AI Agent Instructions: Literature Search Loop (Steps 1–4)

You are a literature search agent. You have been given a **scientific question**.
Your task is to execute the following steps of the search loop to build a clean,
deduplicated pool of relevant papers, which will be handed to downstream extraction
and synthesis steps.

Work through Steps 1–4 in order. Do not skip steps. After Step 4, write the results
to disk and report back a summary.

---

## Scientific Question

> "What mathematical models have been used to study tubuloglomerular feedback
> and its role in renal autoregulation?"

Everything you do flows from this question. Never lose sight of it.

---

## Available Tools

All tools are in `tool/search_utils.py`. Import as needed:

```python
from tool.search_utils import (
    expand_concepts,   # Step 1 — MeSH vocabulary expansion
    run_batch,         # Steps 2–3 — multi-query multi-source retrieval
    deduplicate,       # Step 4 — fuzzy deduplication
    get_mesh_terms,    # (optional) single-concept MeSH lookup
)
```

The CLI runner (`tool/run_search.py --queries-file`) can be used instead of Python
if preferred.

---

## Step 1 — Decompose the Question into Concepts

**Goal:** Produce a MeSH-enriched vocabulary of all relevant concepts and their
synonyms before writing a single query.

### 1.1 — Extract concepts from the question

Read the question and identify 5–10 distinct scientific concepts. Decompose along
these axes:
- **Biological entity** — organ, cell type, transporter, pathway
- **Physiological process** — the mechanism being studied
- **Modeling approach** — ODE, PDE, compartmental, agent-based, etc.
- **Disease/perturbation context** — if present
- **Data analysis** — if present
- **Intervention** — drug, genetic knockout, etc., if present

Write the concept list explicitly before calling any tool. Example:

```
concepts = [
    "tubuloglomerular feedback",
    "renal autoregulation",
    "glomerular filtration rate",
    "proximal tubule",
    "mathematical model",
    "nephron",
    "macula densa",
]
```

### 1.2 — Expand concepts via MeSH

Call `expand_concepts` on the full concept list:

```python
vocab = expand_concepts(concepts, retmax=5)
```

For each concept, inspect the result:
- If `status == "found"`: note the `mesh_name` and the `entry_terms` list.
  These are the **official synonyms, abbreviations, and older terminology**
  that MeSH indexes under this heading. They will seed query variants in Step 2.
- If `status == "not_found"`: the concept may be too recent, too specific, or
  phrased in non-MeSH language. Keep it as a free-text term. Note it for
  synonym expansion by hand (e.g., try abbreviations: TGF, GFR, TF/GFR).

### 1.3 — Build the vocabulary table

Produce a compact table (for your own reasoning; not saved to disk):

| Concept | MeSH name | Key entry terms |
|---|---|---|
| tubuloglomerular feedback | Tubuloglomerular Feedback | TGF, macula densa signal |
| … | … | … |

This table is the **source of truth** for query construction in Step 2.
Do not discard it; refer back to it as you write each query.

### 1.4 — Sanity checks before moving on

- Have you covered every axis (entity, process, model type, disease context)?
- Are there important synonyms the LLM might not have generated?
  (Check: older names, abbreviations, species-specific terms, clinical vs. bench names)
- If the question involves a specific drug or disease, confirm the MeSH preferred name
  (e.g., "Sodium-Glucose Transporter 2 Inhibitors" not just "SGLT2i").

Do not proceed to Step 2 until the vocabulary table is complete.

---

## Step 2 — Generate a Stratified Query Batch

**Goal:** Write 8–12 targeted queries, one per cell of a topic taxonomy, before
retrieving anything.

### 2.1 — Build a topic taxonomy

Construct a simple 2-axis taxonomy from your vocabulary:

```
Rows    = subsystem or process (e.g., TGF signaling, nephron transport, GFR control)
Columns = context modifier  (e.g., mathematical model, ODE, computational, disease)
```

Each cell of the grid is a candidate query slot. Aim for 8–12 non-redundant cells.

Example grid for the TGF question:

| | Mathematical model | Computational | Experimental (control arm) |
|---|---|---|---|
| TGF / macula densa | ✓ | ✓ | |
| Renal autoregulation | ✓ | ✓ | |
| GFR regulation | ✓ | | |
| Proximal tubule transport | ✓ | ✓ | |

### 2.2 — Write one query per cell

For each cell, write a query string. Apply these rules:

**Rule A — Use MeSH terms in PubMed queries.**
Suffix important terms with `[MeSH Terms]` for precision:
```
"Tubuloglomerular Feedback"[MeSH Terms] AND ("mathematical model" OR "computational model")
```

**Rule B — Use entry terms as OR alternatives.**
Add abbreviations and synonyms from your vocabulary table:
```
(TGF OR "tubuloglomerular feedback" OR "macula densa signal") AND kidney AND model
```

**Rule C — Include older terminology** for foundational work that predates modern indexing:
```
"glomerulotubular balance" AND (mathematical OR quantitative OR theoretical)
```

**Rule D — Cover the modeling angle explicitly.**
PubMed and S2 index modeling papers inconsistently. Include all common phrasings:
```
("mathematical model" OR "computational model" OR "ODE model" OR
 "differential equation" OR "simulation") AND "glomerular filtration"
```

**Rule E — Add at least one preprint-oriented query** (shorter, no MeSH syntax,
as bioRxiv/medRxiv do not support field tags):
```
tubuloglomerular feedback model kidney simulation
```

### 2.3 — Assign taxonomy tags

Pair each query with a short tag identifying its taxonomy cell. This tag will be
stored in the `query_tag` field of each retrieved article for provenance tracking.

Format: `{subsystem}:{context}`, e.g.:
```python
query_tags = {
    '"Tubuloglomerular Feedback"[MeSH Terms] AND mathematical model': "TGF:model",
    'renal autoregulation ODE simulation': "autoregulation:model",
    ...
}
```

### 2.4 — Review the batch before retrieval

Read all 8–12 queries side by side and ask:
- Is every taxonomy cell covered?
- Are any two queries nearly identical? If so, merge or replace one.
- Is there at least one query biased toward recent work (last 5 years)?
- Is there at least one query that would catch foundational/classic papers?

Revise until satisfied. Do not start retrieval until the query list is final.

---

## Step 3 — Retrieve from Multiple Sources

**Goal:** Execute the query batch against PubMed, Semantic Scholar, bioRxiv, and
medRxiv. Retrieve `compact=True` (title/authors/year/DOI only — no abstracts) for
the initial broad sweep to minimise token usage.

### 3.1 — Run the batch

```python
articles = run_batch(
    queries=list(query_tags.keys()),
    query_tags=query_tags,
    sources=["pubmed", "semantic_scholar", "biorxiv", "medrxiv"],
    max_per_query=15,        # 15 per query per source = up to 180 raw hits per query
    compact=True,            # no abstracts yet — saves tokens
    year_range=None,         # no year filter on first round; cast a wide net
)
```

> **Note:** `run_batch` already calls `deduplicate` internally. The returned list
> is pre-deduplicated. Step 4 will apply a second, more careful dedup pass
> on the combined result.

### 3.2 — Check retrieval health

After `run_batch` returns, inspect the stderr log lines it emits. Look for:
- `[WARN] ... failed` — a source or query failed entirely. Note which ones.
  If PubMed failed for a query, retry that query alone before continuing.
- `[WARN] medrxiv/biorxiv search unavailable` — the search endpoint fell back to
  date-browse. Fewer results will have been returned. Consider re-running those
  queries manually with a narrower date range.

Count results per source:
```python
from collections import Counter
source_counts = Counter(a["source"] for a in articles)
print(source_counts)
```

If any source returns 0 articles for a query where you expected hits, investigate
before proceeding.

### 3.3 — Retrieve abstracts for high-priority hits (optional)

If Semantic Scholar returned papers with `citation_count > 50`, it is worth fetching
their abstracts now before the dedup step (they are more likely to survive screening):

```python
high_cited = [a for a in articles if a.get("citation_count", 0) > 50]
```

Re-fetch them with `compact=False` using `search_semantic_scholar` or
`fetch_s2_by_ids` on their `ids.paper_id`. This is optional at this stage.

---

## Step 4 — Deduplicate

**Goal:** Produce a clean, unique paper list that correctly collapses preprint and
published-journal versions of the same work.

### 4.1 — Run deduplication

`run_batch` already called `deduplicate` internally, but that was on partial results
query-by-query. Now run it once more on the full combined list to catch any
cross-query duplicates that slipped through:

```python
from tool.search_utils import deduplicate
unique = deduplicate(articles)
```

The deduplicator applies four passes in order:
1. Exact DOI match
2. Alpha-stripped title match (punctuation/case invariant)
3. Word-Jaccard ≥ 0.85 on title word-sets
4. Word-Jaccard ≥ 0.50 + first-author surname match + same year

When two records match, the version from the higher-preference source is kept
(`pubmed > semantic_scholar > biorxiv > medrxiv`).

### 4.2 — Inspect the result

```python
n_before = len(articles)
n_after  = len(unique)
print(f"Dedup: {n_before} → {n_after} ({n_before - n_after} removed)")
```

If `n_before - n_after` is very large (> 30% of input), check whether the Jaccard
threshold is collapsing distinct papers with similar titles. Spot-check 5–10
removed pairs by reviewing the `query_text` provenance field:
```python
# Articles that were retained (check their titles look right)
for a in unique[:5]:
    print(a["title"], "|", a["source"], "|", a.get("query_tag"))
```

If you find false positives (distinct papers incorrectly merged), note them. The
deduplication parameters are in `search_utils.deduplicate` and can be adjusted.

### 4.3 — Save the results

Save the deduplicated list to `brainstorm/` with a descriptive filename:

```python
import json
from pathlib import Path
from datetime import datetime

out_dir = Path("brainstorm")
slug = "search_round_01"
ts   = datetime.now().strftime("%Y%m%d_%H%M")

# JSON (full structured data for downstream steps)
json_path = out_dir / f"{slug}_{ts}.json"
json_path.write_text(
    json.dumps({
        "question": QUESTION,
        "round": 1,
        "queries": list(query_tags.keys()),
        "query_tags": query_tags,
        "n_raw": n_before,
        "n_unique": n_after,
        "articles": unique,
    }, indent=2, ensure_ascii=False),
    encoding="utf-8"
)

# Markdown (human-readable summary)
# Use run_search._format_markdown or write a simple one
md_lines = [f"# Search Round 1\n", f"**Question**: {QUESTION}\n",
            f"**Unique papers**: {n_after}\n\n---\n"]
for i, a in enumerate(unique, 1):
    md_lines.append(
        f"### {i}. [{a['title']}]({a['url']})\n"
        f"{a['source']} | {a.get('year','')} | "
        f"citations: {a.get('citation_count',0)} | "
        f"tag: `{a.get('query_tag','')}`\n"
    )
md_path = out_dir / f"{slug}_{ts}.md"
md_path.write_text("\n".join(md_lines), encoding="utf-8")

print(f"Saved: {json_path}")
print(f"Saved: {md_path}")
```

---

## Output Contract

At the end of Step 4, you must produce and report:

| Output | Format | Contents |
|---|---|---|
| `brainstorm/search_round_NN_TIMESTAMP.json` | JSON | Full article list with provenance, query map, counts |
| `brainstorm/search_round_NN_TIMESTAMP.md` | Markdown | Human-readable summary table |
| Stderr log | text | Per-source counts, warnings, dedup delta |

Report back to the user with:
1. Number of unique papers retrieved
2. Breakdown by source
3. Breakdown by query tag (which cells of the taxonomy were productive)
4. Any retrieval failures or warnings encountered
5. Paths to the saved files

**Do not proceed to Step 5 (screening) without the user's approval.**

---

## Common Failure Modes and How to Handle Them

| Symptom | Likely cause | Action |
|---|---|---|
| A source returns 0 for all queries | Network/API issue | Retry once; skip and note if still failing |
| `[WARN] medrxiv search unavailable` for most queries | bioRxiv/medRxiv search endpoint down | Re-run those queries with `--date-range` flag later |
| Dedup removes > 40% of articles | Jaccard threshold too aggressive, or many true duplicates | Spot-check 10 pairs; if false merges, report to user |
| A query returns > 100 hits | Query too broad | Split into two narrower queries |
| A query returns 0 hits | Query too narrow, MeSH term not indexed | Try free-text version; check for typos |
| MeSH lookup returns `not_found` for a key concept | Concept is emerging/niche | Use free-text + known abbreviations in queries |
