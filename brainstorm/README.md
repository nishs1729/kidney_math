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
│   └── taxonomy/
│       ├── round_01.py          Step-2 query spec that produced round 1
│       └── round_02.py
│
└── data/
    └── papers.jsonl            the shared paper store (all questions, one
                                 JSON object per line) — not meant to be read
                                 directly; see <question-slug>/papers.md instead
```

Produced/maintained by `tool/run_loop.py` (runs a round), `tool/enrich_abstracts.py`
(fetches abstracts for records that don't have one yet), `tool/score_papers.py` +
`tool/apply_scores.py` (relevance scoring), and `tool/fetch_pdfs.py` (PDF
retrieval) — each refreshes the affected `papers.md` files. See
`tool/README.md` for the full toolset.
