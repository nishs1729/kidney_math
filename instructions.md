# AI Agent Instructions: Literature Search Loop (Steps 1–11)

You are a literature search agent. You have been given a **scientific question**.
Your task is to execute the following steps of the search loop to build a clean,
deduplicated pool of relevant papers, get their PDFs on disk, write a structured
scientific summary of each one, score them for relevance, and then **iteratively
refine** the pool: triage by score, expand outward along the citation graph from
what's already confirmed relevant, refresh the taxonomy from what that surfaced,
and loop back to Step 2 — until new rounds stop paying off.

Work through Steps 1–4 in order. Do not skip steps. After Step 4, report back a
summary and get the user's approval before fetching PDFs (Step 5), summarizing
(Step 6), scoring (Step 7), or starting the refinement loop (Step 8 onward) —
see the checkpoints between each.

Steps 5–8 (fetch PDFs, summarize, score, triage) apply to **every round's new
papers** — the first round's search results, and every later round's
citation-expansion or taxonomy-refresh additions alike. Don't treat them as
one-time setup only.

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
| `tool/fetch_pdfs.py` | CLI: downloads PDFs into `brainstorm/<slug>/pdfs/` — open-access first, then institute-network fallback, then a manual-placement check (Step 5) |
| `tool/link_summaries.py` | CLI: scans `brainstorm/<slug>/summaries/` and registers written summaries back into the store (Step 6) |
| `tool/scoring.py` | `heuristic_score`, `similarity_scores` — zero-token metadata/TF-IDF relevance signals (Step 7) |
| `tool/score_papers.py` | CLI: computes and stores `heuristic_score`/`similarity_score` for every paper on a question, prints a ranked table |
| `tool/apply_scores.py` | CLI: persists your rubric-based `relevance_score`/`relevance_rationale` per paper into the store (Step 7) |
| `tool/triage_papers.py` | CLI: buckets papers into `status` core/borderline/excluded from `relevance_score`, writes `core_papers.md` (Step 8) |
| `tool/expand_citations.py` | CLI: pulls backward references + forward citations for core-tier papers via OpenAlex, adds new candidates (Step 9) |
| `tool/round_report.py` | CLI: per-round and per-taxonomy-tag yield report, plus a saturation verdict (Steps 10–11) |

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

## Checkpoint After Step 4

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

**Do not proceed to Step 5 (fetching PDFs) without the user's approval.**

---

## Step 5 — Fetch PDFs

**Goal:** Get full-text PDFs onto disk for as many papers as possible, since
Step 6's summaries should be written from full text wherever available, not
the abstract alone.

### 5.1 — Run it

```bash
python tool/fetch_pdfs.py --question "..." --slug "..." [--limit N]
```

Per paper, this tries (in order): a file already sitting at the expected
destination (see 5.3) → the `pdf_url` already on the record (open access
found during retrieval) → an Unpaywall lookup by DOI (aggregates open-access
copies beyond what OpenAlex/Europe PMC surfaced) → the DOI's publisher
landing page's `citation_pdf_url` meta tag (works for subscribed content
only if the current network is recognized by the publisher as a subscriber
— e.g. an institute connection; otherwise this step just fails closed, it
never attempts to defeat a paywall). Every download is verified to actually
be a PDF (`%PDF` magic bytes / `Content-Type`) before being kept, so a login
or error page is never saved as a false success.

Writes `pdf_path`/`pdf_status` onto each record. Run with `--limit N` first
on a large store to sanity check before fetching everything (it makes one or
more outbound requests per paper, with a politeness delay between them, so
it's slow for large stores).

### 5.2 — Report back and pause for manual downloads

Report counts by `pdf_status`. Papers that end up `unavailable` are listed in
`brainstorm/<slug>/pdfs/_manual_download_needed.md`, one entry per paper with
its DOI/URL and the **exact filename** it must be saved as to be picked up
automatically.

**Stop here and hand this list to the user before proceeding to Step 6.**
They may have access (a personal subscription, an institute connection you
don't have, an author's personal copy) that this script doesn't. Don't
summarize from an abstract-only fallback for a paper still sitting in this
list without giving the user the chance to supply the PDF first.

### 5.3 — Pick up manual downloads

Once the user has placed any PDFs (in `brainstorm/<slug>/pdfs/`, using the
filenames from `_manual_download_needed.md`) — or has told you to proceed
without them — re-run the same command from 5.1, **without `--force`**:

```bash
python tool/fetch_pdfs.py --question "..." --slug "..."
```

A plain re-run only retries papers that are still missing or came back
`unavailable`/`error:*`; anything already downloaded is left untouched, and
any manually-placed file is picked up with no network call and marked
`pdf_status: downloaded:manual`. Repeat 5.2/5.3 as many times as the user
wants; move on once they say so.

---

## Step 6 — Summarize Papers

**Goal:** For every paper, produce a structured scientific summary — written
from the full-text PDF when available, from the abstract alone otherwise —
that Step 7's relevance judgment (and later extraction) can be based on
instead of re-reading raw text each time.

Quality matters more than token cost here: read the actual paper, don't
paraphrase the abstract into extra paragraphs. That said, don't waste effort
either — see the batching note in 6.2.

### 6.1 — Summary template

One Markdown file per paper, at `brainstorm/<slug>/summaries/<file>.md`,
using the exact filename `tool.paper_store.safe_filename(paper_id, ".md")`
would produce for that paper's `id` (so `link_summaries.py` in 6.3 can find
it — check with `python -c "from tool.paper_store import safe_filename; print(safe_filename('<id>', '.md'))"`
if unsure). Structure (adjust section names/count to what the paper actually
offers — don't force a section that has nothing to say):

```markdown
# <Title>

**Source:** <full text | abstract only — no PDF available>
**Authors:** ... | **Year:** ... | **Venue:** ... | **DOI:** ...

## Aim / Research Question
What the paper set out to do.

## Modeling Approach
The actual equations/system type, key assumptions, what's novel about the
formalism — this is the section a generic summary would skip, and the one
this survey cares about most. Omit if the paper isn't itself a modeling paper
(e.g. a pure experimental or review paper) rather than forcing content here.

## Key Results
The paper's main findings.

## Discussion / Interpretation
What the authors think their results mean.

## Limitations / Problems
What the authors themselves flag as weak, unresolved, or out of scope.

## Gaps / Open Questions
Explicit or implied directions not covered by this paper — feeds into
taxonomy refinement (`process.md` Step 9) later, separate from evaluation.

## Relevance to Our Question
One paragraph explicitly tying this paper back to the scientific question at
the top of this document. This is the paragraph Step 7 leans on most.
```

If `pdf_status` isn't a `downloaded:*` value, write the summary from the
abstract alone and set **Source: abstract only — no PDF available** — don't
skip the paper, but don't invent Modeling-Approach/Results detail an abstract
doesn't support either; shorter sections (or omitting one entirely) are
correct in that case, not a gap to paper over.

### 6.2 — Work in batches, PDF-read via the `Read` tool

Read a downloaded PDF directly (page-ranged if it's long) — no extra library
needed. Process in batches (e.g. 10–15 papers), starting from whatever
ordering is available (existing `heuristic_score`/`similarity_score` from a
prior round, or just `pdf_status: downloaded:*` before `unavailable`/
abstract-only). After the **first** batch, pause and show the user 1–2
example summaries before continuing to the rest, in case the template or
depth needs adjusting — cheaper to fix after 10 papers than after 200.

### 6.3 — Register the summaries

After each batch (or at the end):

```bash
python tool/link_summaries.py --question "..." --slug "..."
```

Scans `brainstorm/<slug>/summaries/`, sets `summary_path`/`summary_status`
on every matching record, refreshes `papers.md`, and reports how many
summaries are written vs. still missing — use this count to track progress
across batches instead of re-deriving it by hand.

### 6.4 — Report back

Report: how many papers were summarized (full-text vs. abstract-only split),
how many are still missing (if stopping partway through a large store), and
anything notable found while reading (a paper that turned out off-topic
despite matching the query, a recurring gap across papers, etc.).

---

## Step 7 — Score Papers for Relevance

**Goal:** Rank every paper in the store for this question by how important it
is *for answering the scientific question*, using the Step 6 summary (richer
than the abstract alone) as the primary evidence. This is scoring/ranking
only — nothing gets deleted or excluded at this step.

Quality of the final judgment matters more than the tokens spent getting
there; the point of splitting this into two passes below is to spend tokens
only on the judgment call itself, not on mechanical bookkeeping (re-deriving
a ranking, hand-formatting JSONL edits) that Python can do for free.

### 7.1 — Compute cheap prescreen signals (zero tokens)

```bash
python tool/score_papers.py --question "..." --slug "..."
```

This writes two fields onto every matching store record and prints a ranked
table:
- `heuristic_score` (0–100): citation velocity (citations/year, log-scaled),
  how many distinct taxonomy tags the paper matched across rounds (a paper
  that keeps resurfacing under different cells is more central to the
  question), and a flat review-article boost.
- `similarity_score` (0–1): TF-IDF cosine similarity between the question
  text and the paper's title+abstract — bag-of-words lexical overlap, no
  embedding API involved. This catches shared terminology but **misses
  paraphrase and synonym matches** (e.g. a paper that says "distal feedback
  loop" instead of "tubuloglomerular feedback" scores low here even if
  it's highly relevant) — treat it as one signal, not the verdict.

Neither signal reads the actual argument of the paper. Use them to decide
review order, not inclusion/exclusion.

### 7.2 — Rubric-based relevance pass (your judgment)

Read each paper's **summary** (`summary_path`, from Step 6) if it has one —
it already distills the full text, so re-reading the PDF here is redundant.
Only fall back to the raw abstract for a paper Step 6 didn't reach yet. Start
from `score_papers.py`'s ranked output, but don't stop at an arbitrary
cutoff — a paper with a low heuristic/similarity score can still be highly
relevant, so skim past the top of the ranking rather than truncating it.
Batch this (e.g. 15–25 summaries read together) rather than one round-trip
per paper.

For each paper, assign:
- `relevance_score` — your judgment of how important this paper is for
  answering the scientific question. Use a 0–5 scale unless the user asks
  for something else (0 = off-topic despite matching the query; 5 = directly,
  substantially relevant).
- `relevance_rationale` — one sentence on *why* (not a summary of the
  abstract/summary — the reason for the score).

Write these into a JSON file (paper id → `{relevance_score, relevance_rationale}`;
ids are the `id` field already on each store record, e.g. `"doi:10.1234/..."`)
and persist them:

```bash
python tool/apply_scores.py scores.json --question "..." --slug "..."
```

This overwrites `relevance_score`/`relevance_rationale` on the named records
(unlike `paper_store.upsert`, which only ever fills gaps — a re-score here is
an explicit correction) and refreshes `papers.md`, now sorted with
`relevance_score` taking priority over the cheap prescreen signals.

### 7.3 — Report back

Report: how many papers were scored, the relevance-score distribution (e.g.
"12 at 5, 30 at 3–4, ~150 at 0–2"), how many were scored from a full summary
vs. abstract-only fallback, and anything the heuristic/similarity signals got
notably wrong (informs whether to trust them more or less next round).

---

## Checkpoint After Step 7

At the end of Step 7, in addition to the Step 4 checkpoint outputs above:

| Output | Format | Contents |
|---|---|---|
| `brainstorm/<slug>/papers.md` | Markdown | Sorted by `relevance_score` (or the cheap signals if unscored), with score/rationale/pdf-status/summary-link shown per paper |
| `brainstorm/<slug>/pdfs/*.pdf` | PDF | Downloaded full texts, filenamed by paper id |
| `brainstorm/<slug>/pdfs/_manual_download_needed.md` | Markdown | Papers that need manual PDF retrieval (only present if non-empty) |
| `brainstorm/<slug>/summaries/*.md` | Markdown | One structured summary per paper, filenamed by paper id |
| `brainstorm/data/papers.jsonl` | JSONL | Now also carries `pdf_path`, `pdf_status`, `summary_path`, `summary_status`, `heuristic_score`, `similarity_score`, `relevance_score`, `relevance_rationale` |

Report the `relevance_score` distribution (as in 7.3).

**Do not start the refinement loop (Step 8 onward) without the user's approval.**

---

## Step 8 — Triage

**Goal:** Turn `relevance_score` into an actionable bucket per paper, so
citation expansion (Step 9) knows which papers to expand from, and so the
corpus stays navigable as rounds accumulate. Nothing is deleted here —
`excluded` papers keep full provenance in the store.

```bash
python tool/triage_papers.py --question "..." --slug "..." [--core-min 4] [--exclude-max 1]
```

Writes `status` (`core` / `borderline` / `excluded` / `unscored`) onto every
scored record and writes `brainstorm/<slug>/core_papers.md` — the working
set for everything downstream (citation expansion now, extraction later).
The default thresholds (core ≥ 4, excluded ≤ 1 on the 0–5 scale from 7.2)
are a reasonable starting point; tighten `--core-min` if the core tier comes
back too large to expand citations from usefully.

Report back: the core/borderline/excluded/unscored counts.

---

## Step 9 — Expand Citations

**Goal:** Follow the citation graph outward from papers already confirmed
relevant (`status: core`) to find papers that keyword search structurally
can't — different terminology, older papers indexed differently, work in an
adjacent subfield. This is usually the single highest-yield step in the
whole loop (`process.md` Step 8).

```bash
python tool/expand_citations.py --question "..." --slug "..." --round <N> [--max-per-paper 25]
```

`<N>` should be the next unused round number (e.g. `2` after Step 3's round
1). For each core-tier paper, this pulls up to `--max-per-paper` backward
references (what it cites) and forward citations (what cites it) via
OpenAlex, backfilling citation-graph fields via a DOI lookup first for core
papers that didn't originally come from OpenAlex. New candidates are added
to the store tagged `query_tag = "citation:backward"` or `"citation:forward"`,
deduplicated the same way a normal search round is.

**These new papers still need Steps 5–8 run on them** (PDF fetch, summarize,
score, triage) before they can seed a further round of expansion or inform
Step 10 — they enter the loop at the same point round 1's papers did, they
just came from citations instead of a taxonomy query.

Report back: backward/forward counts fetched, count after dedup, and how
many were genuinely new to the store.

---

## Step 10 — Refresh the Taxonomy and Loop Back

**Goal:** Use what this round surfaced to write a better taxonomy for the
next round, instead of re-running the same queries (`process.md` Steps 9–10).

Two inputs, both already on disk:
- **Unproductive taxonomy cells** —
  ```bash
  python tool/round_report.py --question "..." --slug "..." --by-tag
  ```
  shows total/core counts per `query_tag`. A cell with a lot of hits and no
  core papers is too broad or off-target; narrow it or drop it. A cell with
  very few hits may need a broader OR-term.
- **Gaps / Open Questions** — read this section across the core papers'
  summaries (Step 6). Recurring terminology or subtopics mentioned there but
  absent from the current taxonomy's `or_terms` are exactly what a new
  taxonomy row/column should cover.

Write the refined taxonomy to `brainstorm/<slug>/taxonomy/round_NN.py` (next
round number) following Step 2's rules, then go back to **Step 2** with it —
Steps 2–4 produce this round's new search-based candidates the same way
round 1 did, in parallel with (or after) Step 9's citation-expansion
candidates for the same round number.

---

## Step 11 — Check Saturation

**Goal:** Decide whether another round is worth running, instead of looping
indefinitely (`process.md` Step 11).

```bash
python tool/round_report.py --question "..." --slug "..."
```

Reports new-papers and new-core-papers per round and a verdict: `SATURATED`
once the last couple of rounds each contributed fewer than a few new core
papers (tune with `--saturation-rounds`/`--saturation-threshold`).

Report the verdict to the user along with the current `core_papers.md`
count. **Do not start another loop iteration (Step 9/10) without the user's
approval** — whether or not the tool says saturated, stopping or continuing
is the user's call, not something to decide unilaterally.

---

## Output Contract

At the end of a loop iteration, in addition to the Step 7 checkpoint outputs above:

| Output | Format | Contents |
|---|---|---|
| `brainstorm/<slug>/core_papers.md` | Markdown | Just the `status: core` tier — the working set for extraction |
| `brainstorm/<slug>/taxonomy/round_NN.py` | Python | Each round's taxonomy spec, refined from the previous round's yield |
| `brainstorm/data/papers.jsonl` | JSONL | Now also carries `status`, `referenced_works`, `cited_by_api_url` |

**Do not proceed to structured field extraction (`process.md` Step 7 — full
field extraction from `core_papers.md`, a separate step from this document's
own Step 7, which is scoring) without the user's approval.**

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
| `similarity_score` is low on a clearly relevant paper | TF-IDF is lexical, not semantic — the paper uses different terminology than the question | Don't exclude on this signal alone; trust the rubric read (5.2) over it |
| `fetch_pdfs.py` reports mostly `unavailable` | Papers are paywalled and the current network isn't recognized as a subscriber (no institute VPN/IP) | Expected outside the institute network; use `_manual_download_needed.md` for manual retrieval |
| A manually-placed PDF isn't picked up on re-run | Filename doesn't exactly match `_manual_download_needed.md`'s "Save as" value | Rename it to match exactly (`paper_store.safe_filename` is the source of truth) |
| PDF text looks garbled or empty when read | Scanned image PDF with no text layer (common for old papers), or a corrupted download | Fall back to summarizing from the abstract, note it in **Source**, and consider deleting/re-fetching the file |
| `link_summaries.py` still shows a paper "missing" after you wrote its file | Filename doesn't match `safe_filename(paper_id, ".md")` for that record's `id` | Check the id in `papers.jsonl`/`papers.md` and rename the summary file to match exactly |
| `expand_citations.py` errors "no seed papers to expand from" | `triage_papers.py` hasn't been run yet, or no paper scored `core` | Run Step 8 first, or lower `--core-min`, or pass `--min-relevance` to bypass triage |
| `expand_citations.py` finds few/no backward references for a paper | The seed paper isn't in OpenAlex and has no DOI to backfill from (e.g. an old paper only indexed in PubMed) | Expected for some papers; forward citations may still work if it has a DOI |
| `round_report.py` never reports `SATURATED` | Citation expansion keeps surfacing new core papers each round | That's the loop working as intended — keep going while it's productive, or stop earlier by user judgment regardless of the verdict |
