# brainstorm/

Output of the literature search loop (see `process.md` and `instructions.md`
at the repo root). Only human-meaningful material lives directly in this
directory; everything else is encapsulated in a subfolder.

```
brainstorm/
├── <question-slug>/           one folder per research question
│   ├── README.md               full question text
│   ├── papers.md                human-readable results, ranked by relevance
│   ├── pdfs/
│   │   ├── *.pdf                 downloaded full texts
│   │   └── _manual_download_needed.md
│   ├── summaries/
│   │   └── *.md                  one structured summary per paper
│   ├── core_papers.md            just the status:core tier
│   └── taxonomy/
│       ├── round_01.py          Step-2 query spec that produced round 1
│       └── round_02.py          refined from round 1's yield, each loop iteration
│
└── data/
    └── papers.jsonl            the shared paper store (all questions, one
                                 JSON object per line) — not meant to be read
                                 directly; see <question-slug>/papers.md instead
```

Produced/maintained, in pipeline order, by `tool/run_loop.py` (runs a round),
`tool/enrich_abstracts.py` (fetches abstracts), `tool/fetch_pdfs.py` (PDF
retrieval, with a manual-download checkpoint), `tool/link_summaries.py`
(registers hand-written paper summaries), `tool/score_papers.py` +
`tool/apply_scores.py` (relevance scoring, informed by those summaries), and
— closing the loop — `tool/triage_papers.py` (buckets by score),
`tool/expand_citations.py` (pulls new candidates from the citation graph of
the core tier), and `tool/round_report.py` (per-round/per-tag yield and a
saturation verdict, to decide whether another round is worth running) —
each refreshes the affected `papers.md` files. See `tool/README.md` for the
full toolset and `instructions.md` for the step-by-step process.
