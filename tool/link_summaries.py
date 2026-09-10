#!/usr/bin/env python3
"""
link_summaries.py — Register written paper summaries back into the store.

Summaries are written by whoever runs the loop (see instructions.md Step 6):
reading a paper's PDF (or its abstract, if no PDF is available) and writing
a structured Markdown summary to brainstorm/<slug>/summaries/<file>.md. This
script does no summarizing itself — it just scans that directory, and for
every paper in the store whose expected summary file exists (same id ->
filename convention as fetch_pdfs.py, via paper_store.safe_filename), sets:

    summary_path    path to the summary .md, relative to the repo root
    summary_status  "written"

and refreshes brainstorm/<slug>/papers.md so the summary link shows up
there. Papers whose file isn't present are reported as still missing (and
get summary_status "missing" if they didn't have one already) so you can
track batch-by-batch progress across a large store without re-deriving it
by hand each time.

Usage:
    python tool/link_summaries.py --question "..." [--slug ...]
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.paper_store import DEFAULT_STORE_PATH, BRAINSTORM_ROOT, load, save, export_question, question_dir, safe_filename


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    args = ap.parse_args()

    store_path = Path(args.store_path)
    store = load(store_path)
    records = [r for r in store.values() if r.get("question") == args.question]
    if not records:
        sys.stderr.write(f"[ERROR] no papers in store for question: {args.question!r}\n")
        sys.exit(1)

    qdir = question_dir(args.question, args.slug, BRAINSTORM_ROOT)
    summaries_dir = qdir / "summaries"

    newly_written = 0
    still_missing = []

    for r in records:
        dest = summaries_dir / safe_filename(r["id"], ".md")
        if dest.exists():
            if r.get("summary_status") != "written":
                newly_written += 1
            r["summary_path"] = str(dest.relative_to(_REPO_ROOT))
            r["summary_status"] = "written"
        else:
            r["summary_status"] = "missing"
            still_missing.append(r)

    save(store, store_path)
    export_question(store, args.question, args.slug)

    written_total = len(records) - len(still_missing)
    print(f"Summaries written: {written_total}/{len(records)} ({newly_written} newly linked this run)")
    if still_missing:
        print(f"Still missing ({len(still_missing)}):")
        for r in still_missing[:20]:
            print(f"  - {r.get('title', '(no title)')[:90]}  [{r['id']}]")
        if len(still_missing) > 20:
            print(f"  ... and {len(still_missing) - 20} more")


if __name__ == "__main__":
    main()
