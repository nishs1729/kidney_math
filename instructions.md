# AI Agent Instructions: Literature Search Loop (Steps 1–4)

You are a literature search agent. You have been given a **scientific question**.
Your task is to execute the following steps of the search loop to build a clean,
deduplicated pool of relevant papers, which will be handed to downstream extraction
and synthesis steps.

Work through Steps 1–4 in order. Do not skip steps. After Step 4, report back a
summary — the results themselves are already on disk (see Output Contract).

---

## Scientific Question

> "What mathematical models have been used to study tubuloglomerular feedback
> and its role in renal autoregulation?"

Everything you do flows from this question. Never lose sight of it.

---

## Available Tools

| Module | What it's for |
|---|---|
| `tool/search_utils.py` | `expand_concepts`, `get_mesh_terms` (Step 1); `search_pubmed`, `search_openalex`, `search_biorxiv`, `search_medrxiv`, `search_all`, `run_batch` (Steps 2–3); `deduplicate` (Step 4) |
| `tool/query_utils.py` | `build_queries` (Step 2 — assembles a taxonomy grid into query strings + tags) and `lint_query` (catches broken queries before they run) |
| `tool/paper_store.py` | `load`/`upsert`/`export_question` — the persistent, human-readable paper store (Step 4) |
| `tool/run_loop.py` | CLI that runs Steps 2–4 end-to-end from a taxonomy spec and writes into the paper store |
| `tool/enrich_abstracts.py` | CLI follow-up: fetches abstracts for store records that don't have one yet |

Sources are **PubMed, OpenAlex, bioRxiv, and medRxiv** — there is no Semantic
Scholar integration (its unauthenticated API is unusably rate-limited; OpenAlex
is the free, keyless replacement for citation counts).

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
- If `status == "found"`: **don't take `mesh_name` at face value — check that it
  actually matches the concept before using it.** Not every scientific concept
  has its own MeSH descriptor; when it doesn't, PubMed's own indexing silently
  falls back to whatever generic heading shares a word with your search term.
  (Concretely: querying MeSH for "tubuloglomerular feedback" does *not* return
  a "Tubuloglomerular Feedback" descriptor — there isn't one — it returns
  generic entries like "Feedback" or "Neurofeedback" that only share the word
  "feedback." Read the `scope_note` before trusting `mesh_name`.) When it's a
  real match, note the `entry_terms` — the official synonyms, abbreviations,
  and older terminology MeSH indexes under that heading — for Step 2.
- If `status == "not_found"` (or `found` but clearly the wrong concept per the
  check above): treat it as free text. Note it for synonym expansion by hand
  (e.g., try abbreviations: TGF, GFR, TF/GFR) — but see the abbreviation
  warning in Step 2 before using a bare acronym.

### 1.3 — Build the vocabulary table

Produce a compact table (for your own reasoning; not saved to disk):

| Concept | MeSH name | Verified match? | Key entry terms / free-text synonyms |
|---|---|---|---|
| tubuloglomerular feedback | *(none — falls back to generic "Feedback")* | no | TGF, macula densa signal, glomerulotubular balance (older/related term) |
| glomerular filtration rate | Glomerular Filtration Rate | yes | GFR |
| … | … | … | … |

This table is the **source of truth** for query construction in Step 2.
Do not discard it; refer back to it as you write each row's `or_terms`.

### 1.4 — Sanity checks before moving on

- Have you covered every axis (entity, process, model type, disease context)?
- For every `status == "found"` concept, does `mesh_name`/`scope_note` actually
  describe what you meant, or just share a word with your search term?
- Are there important synonyms the LLM might not have generated?
  (Check: older names, abbreviations, species-specific terms, clinical vs. bench names)
- If the question involves a specific drug or disease, confirm the MeSH preferred name
  (e.g., "Sodium-Glucose Transporter 2 Inhibitors" not just "SGLT2i").

Do not proceed to Step 2 until the vocabulary table is complete.

---

## Step 2 — Build a Taxonomy and Let `build_queries` Assemble the Queries

**Goal:** Write a taxonomy grid (rows × columns), then let `query_utils.build_queries`
mechanically turn it into 8–12 query strings — don't hand-type each query string
directly, and don't bypass the linter it runs.

### 2.1 — Build a topic taxonomy

```
Rows    = subsystem or process (e.g., TGF signaling, nephron transport, GFR control)
Columns = context modifier  (e.g., mathematical model, computational, review)
```

Each cell of the grid is a candidate query slot. Aim for 8–12 non-redundant cells.

### 2.2 — Express the taxonomy as rows/columns, not raw query strings

Write it as a Python structure — either inline, or (preferred, for provenance)
saved to `brainstorm/<slug>/taxonomy/round_NN.py` as `ROWS`/`COLUMNS`/`EXTRA_AND`
(see `tool/run_loop.py`'s docstring):

```python
rows = [
    {"tag": "TGF", "or_terms": ['"tubuloglomerular feedback"', '"macula densa signal"']},
    {"tag": "autoregulation", "or_terms": ['"renal autoregulation"']},
    {"tag": "GFR", "or_terms": ['"Glomerular Filtration Rate"[MeSH Terms]']},
]
columns = {
    "model": '("mathematical model" OR "computational model" OR "differential equation")',
    "classic": '(mathematical OR quantitative OR theoretical)',   # catches foundational work; pair with older-terminology or_terms
}
extra_and = "kidney"
```

Rules for what goes into `or_terms`/`columns`:

**Rule A — Use MeSH terms where Step 1 found a real match**, suffixed with
`[MeSH Terms]` for precision — only for concepts you verified in 1.4, not a
loose fallback match.

**Rule B — Use entry terms/synonyms as additional OR alternatives**, but
**never add a bare short abbreviation on its own** (no standalone `TGF`, `GFR`,
etc., as an OR term). Short acronyms are heavily overloaded in the literature —
`TGF` overwhelmingly means *transforming growth factor beta* in a full-text
index, not tubuloglomerular feedback — and a bare one will pull in unrelated
papers once matched against bioRxiv/medRxiv/OpenAlex's full-text search. Spell
it out (`"tubuloglomerular feedback"`) or require it to co-occur with a second,
disambiguating term instead.

**Rule C — Include older terminology** (e.g. `"glomerulotubular balance"`) in
its own row or alongside the modern term, for foundational work that predates
current indexing.

**Rule D — Cover the modeling angle explicitly** — include all common
phrasings (`"mathematical model"`, `"computational model"`, `"ODE model"`,
`"differential equation"`, `"simulation"`) in a shared column so every row
gets the same modeling coverage.

**Rule E — Add at least one preprint-friendly row/column combination**: short,
free-text, no field-tag syntax (bioRxiv/medRxiv and OpenAlex don't support
`[MeSH Terms]`).

**Rule F — Add a review-coverage column** using
`query_utils.review_clause("pubmed")` (`"Review"[Publication Type]`) — reviews
aren't caught automatically otherwise. Europe PMC (bioRxiv/medRxiv) also
supports this via `review_clause("biorxiv")` (`PUB_TYPE:"Review"`); OpenAlex
doesn't expose publication type precisely enough to filter on, so don't expect
this column to do anything useful there.

### 2.3 — Assemble and lint

```python
from tool.query_utils import build_queries

query_tags = build_queries(rows=rows, columns=columns, extra_and=extra_and)
```

`build_queries` lints every generated query (balanced quotes/parens, no quoted
phrase directly followed by a bare word with no AND/OR/NOT, no bare short
abbreviation) and **raises a `ValueError` naming the specific problem** if
anything fails — it will not silently hand back a broken query. If it raises,
fix the offending row/column's `or_terms`/clause; don't work around the
linter.

The `{row_tag}:{column_tag}` tag (e.g. `"TGF:model"`) is stored automatically
in the `query_tag` field of each retrieved article for provenance tracking.

### 2.4 — Review the batch before retrieval

Read all 8–12 queries side by side (`list(query_tags.items())`) and ask:
- Is every taxonomy cell covered?
- Are any two queries nearly identical? If so, merge or replace one.
- Is there at least one query biased toward recent work (last 5 years)?
- Is there at least one query that would catch foundational/classic papers (Rule C)?

Revise until satisfied. Do not start retrieval until the query list is final.

---

## Step 3 — Retrieve from Multiple Sources

**Goal:** Execute the query batch against PubMed, OpenAlex, bioRxiv, and
medRxiv, and persist the results into the paper store. `compact=True` (no
abstracts) is used for this broad sweep to minimize token usage.

### 3.1 — Run it

Preferred: the CLI, which runs Steps 2–4 in one call and writes straight into
the paper store instead of an ad hoc script:

```bash
python tool/run_loop.py \
    --question "What mathematical models have been used to study tubuloglomerular feedback and its role in renal autoregulation?" \
    --slug tgf-renal-autoregulation \
    --round 1
```

(`--slug` should be a short, hand-picked kebab-case name — don't rely on the
default, which just truncates the raw question text and tends to keep its
generic opening words rather than the actual topic.)

Equivalent, if you need to work in Python directly:

```python
from tool.search_utils import run_batch

articles = run_batch(
    queries=list(query_tags.keys()),
    query_tags=query_tags,
    sources=["pubmed", "openalex", "biorxiv", "medrxiv"],
    max_per_query=15,
    compact=True,
    year_range=None,   # no year filter on first round; cast a wide net
)
```

> `run_batch` already calls `deduplicate` internally on the combined result
> (Step 4's first pass is already done by the time it returns).

### 3.2 — Check retrieval health

Inspect the stderr log lines `run_loop.py`/`run_batch` emit. Look for:
- `[WARN] ... failed` — a source or query failed entirely. Note which ones.
  If PubMed failed for a query, retry that query alone before continuing.
- `[WARN] HTTP 503 ... openalex` — OpenAlex's anonymous-search tier
  occasionally pauses under load and asks you to retry; this is transient
  infra on their end, not a query problem. Retry once; if it's persistent,
  set `OPENALEX_EMAIL` in `.env` (moves you into their faster "polite pool")
  and try again.

Count results per source (`run_loop.py` prints this automatically; in Python:
`Counter(a["source"] for a in articles)`). If any source returns 0 articles
for a query where you expected hits, investigate before proceeding.

### 3.3 — Abstracts are deferred, not skipped

Don't hand-pick "high-citation" articles to enrich mid-round. Once Step 4 has
settled the store for this round, run the abstract backfill over everything
newly added at once:

```bash
python tool/enrich_abstracts.py
```

This batches PubMed lookups by PMID and looks up bioRxiv/medRxiv abstracts by
DOI via Europe PMC, then refreshes the affected `papers.md` — cheap enough to
just run over the whole store rather than filtering by citation count first.

---

## Step 4 — Deduplicate & Persist

**Goal:** Produce a clean, unique paper list that correctly collapses preprint
and published-journal versions of the same work, and keep it durably across
rounds instead of a fresh file each time.

If you used `run_loop.py` in Step 3, the within-round part of this step
already happened: `run_batch` accumulates every query's results for the
*entire* batch first and calls `deduplicate` **once, on that combined list**
— not per query — so there's no separate "second pass" to run yourself within
a round; running `deduplicate` again on the exact same list is a no-op.

What `run_loop.py` does *not* do automatically is fuzzy-match a paper against
ones already in the store **from an earlier round** — `paper_store.upsert`
(which it calls next) only recognizes a paper it's seen before by exact DOI
or exact alpha-only title match (see `paper_store.paper_id`), not the fuzzy
Jaccard/author+year passes below. In practice this is rarely an issue (a
paper's DOI or title don't usually shift between rounds), but if you suspect
a preprint-vs-published pair slipped past this cheaper cross-round check,
spot-check it manually.

### 4.1 — How dedup works

```python
from tool.search_utils import deduplicate
unique = deduplicate(articles)
```

Four passes, in order:
1. Exact DOI match
2. Alpha-stripped title match (punctuation/case invariant)
3. Word-Jaccard ≥ 0.85 on title word-sets
4. Word-Jaccard ≥ 0.50 + first-author surname match + same year

When two records match, the version from the higher-preference source is kept
(`pubmed > openalex > biorxiv > medrxiv`). This is what `run_batch` already
ran for you within the round; call it yourself only if you're merging
articles from multiple independent `run_batch`/`search_all` calls that
haven't been combined yet.

### 4.2 — Inspect the result

```python
n_before = len(articles)
n_after  = len(unique)
print(f"Dedup: {n_before} → {n_after} ({n_before - n_after} removed)")
```

If `n_before - n_after` is very large (> 30% of input), check whether the Jaccard
threshold is collapsing distinct papers with similar titles. Spot-check 5–10
removed pairs by reviewing the `query_text` provenance field.

If you find false positives (distinct papers incorrectly merged), note them. The
deduplication parameters are in `search_utils.deduplicate` and can be adjusted.

### 4.3 — Persist into the paper store

```python
from tool.paper_store import load, upsert, export_question

store = load()
new_ids = upsert(store, unique, question=QUESTION, round_num=1, slug="tgf-renal-autoregulation")
export_question(store, QUESTION, slug="tgf-renal-autoregulation")
```

This writes:
- `brainstorm/data/papers.jsonl` — the shared store (all questions, one JSON
  object per paper per line; not meant to be read directly)
- `brainstorm/<slug>/README.md` — the question text
- `brainstorm/<slug>/papers.md` — human-readable results for this question

A paper already in the store from an earlier round keeps its accumulated
fields (e.g. an abstract fetched since) and just gains the new round's
`query_tag`; running the same question again only reports genuinely new
papers.

---

## Output Contract

At the end of Step 4, the following are already on disk:

| Output | Format | Contents |
|---|---|---|
| `brainstorm/<slug>/README.md` | Markdown | The question text |
| `brainstorm/<slug>/papers.md` | Markdown | Human-readable results for this question |
| `brainstorm/data/papers.jsonl` | JSONL | Full article records with provenance, across all questions |
| Stderr log | text | Per-source counts, warnings, dedup delta |

Report back to the user with:
1. Number of unique papers retrieved, and how many were genuinely new to the store this round
2. Breakdown by source
3. Breakdown by query tag (which cells of the taxonomy were productive)
4. Any retrieval failures or warnings encountered
5. Paths to `brainstorm/<slug>/README.md` and `papers.md`

**Do not proceed to Step 5 (screening) without the user's approval.**

---

## Common Failure Modes and How to Handle Them

| Symptom | Likely cause | Action |
|---|---|---|
| `build_queries` raises `ValueError` | A generated query has a bare ambiguous abbreviation, or a quoted phrase directly followed by a bare word with no AND/OR/NOT | Fix the named row/column's `or_terms`/clause (spell out the abbreviation, or fold the trailing word into the phrase). Don't bypass the linter. |
| A source returns 0 for all queries | Network/API issue, or (OpenAlex specifically) a transient anonymous-tier 503 | Retry once; for OpenAlex, set `OPENALEX_EMAIL`; skip and note if still failing |
| `expand_concepts` returns `status: "found"` but `mesh_name` doesn't match the concept | The concept has no MeSH descriptor of its own; the lookup fell back to a loosely related heading | Check `scope_note`; if it's not a real match, treat the concept as free text (Step 1.2) |
| Dedup removes > 40% of articles | Jaccard threshold too aggressive, or many true duplicates | Spot-check 10 pairs; if false merges, report to user |
| A query returns > 100 hits | Query too broad — full-text sources (Europe PMC, OpenAlex) especially can return a lot on a loosely-anchored query | Add a disambiguating AND term, or split into two narrower queries |
| A query returns 0 hits | Query too narrow, MeSH term not indexed | Try the free-text version; check for typos |
