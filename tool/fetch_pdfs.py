#!/usr/bin/env python3
"""
fetch_pdfs.py — Download PDFs for papers in the store into brainstorm/<slug>/pdfs/.

Tries, per paper, in order:
  0. A file already sitting at the expected destination path — trusted as-is
     (after verifying it's really a PDF) with no network call. This is what
     makes the manual-download workflow below "just re-run the script."
  1. `pdf_url` already on the record (open-access location found by OpenAlex
     or Europe PMC during retrieval).
  2. Unpaywall (api.unpaywall.org) lookup by DOI — aggregates open-access
     copies (repository, publisher hybrid-OA, etc.) beyond what OpenAlex/
     Europe PMC surfaced directly.
  3. If still not found and the DOI resolves to a publisher page: fetch that
     page and look for a `<meta name="citation_pdf_url" content="...">` tag
     (the same mechanism reference managers like Zotero use). Downloading the
     PDF itself then relies on your network already being recognized as a
     subscriber by the publisher (institute IP/VPN) — this script does not
     do anything beyond a plain HTTP GET, no login/paywall bypass.

Every attempt is verified to actually be a PDF (checks the response's
Content-Type header and the file's %PDF magic bytes) before it's kept, so a
login page or error page never gets saved as a fake "success".

Writes back onto each record:
    pdf_path    path to the saved file, relative to the repo root
    pdf_status  "downloaded:open_access" | "downloaded:institute" |
                "downloaded:manual" | "unavailable" | "error:<message>"

Papers that end up "unavailable" are also listed in
brainstorm/<slug>/pdfs/_manual_download_needed.md (title, DOI, URL, and the
exact filename to save as) so they can be fetched by hand — save the file
under that name in the same pdfs/ directory, then re-run this script (no
flags needed) to have it picked up and marked "downloaded:manual".

Usage:
    python tool/fetch_pdfs.py --question "..." [--slug ...] [--limit N] [--force]
"""

import argparse
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tool.paper_store import DEFAULT_STORE_PATH, BRAINSTORM_ROOT, load, save, question_dir, safe_filename

_USER_AGENT = "LiteratureSurveyTool/1.0 (agent-literature-survey)"
_UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
_REQUEST_DELAY = 1.0  # politeness delay between outbound requests


def _request(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 30) -> Optional[Tuple[int, Dict[str, str], bytes]]:
    hdrs = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"[WARN] HTTP {e.code} fetching {url}\n")
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        sys.stderr.write(f"[WARN] network error fetching {url}: {e}\n")
        return None


def _looks_like_pdf(headers: Dict[str, str], body: bytes) -> bool:
    ctype = headers.get("content-type", "")
    if "pdf" in ctype.lower():
        return True
    return body[:5] == b"%PDF-"


def _try_download(url: str) -> Optional[bytes]:
    result = _request(url)
    time.sleep(_REQUEST_DELAY)
    if result is None:
        return None
    status, headers, body = result
    if status != 200 or not body:
        return None
    if not _looks_like_pdf(headers, body):
        return None
    return body


def _unpaywall_pdf_url(doi: str, email: str) -> Optional[str]:
    url = f"{_UNPAYWALL_BASE}/{urllib.parse.quote(doi, safe='')}?{urllib.parse.urlencode({'email': email})}"
    result = _request(url)
    time.sleep(_REQUEST_DELAY)
    if result is None:
        return None
    status, _headers, body = result
    if status != 200:
        return None
    import json

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    loc = data.get("best_oa_location") or {}
    return loc.get("url_for_pdf") or loc.get("url") or None


def _citation_pdf_url_from_landing_page(doi: str) -> Optional[str]:
    result = _request(f"https://doi.org/{urllib.parse.quote(doi, safe='/')}")
    time.sleep(_REQUEST_DELAY)
    if result is None:
        return None
    _status, _headers, body = result
    html = body.decode("utf-8", errors="replace")
    for tag in re.findall(r"<meta[^>]+>", html, re.IGNORECASE):
        if "citation_pdf_url" not in tag.lower():
            continue
        m = re.search(r'content=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def fetch_one(record: Dict[str, Any], out_dir: Path) -> Tuple[str, Optional[str]]:
    """Returns (pdf_status, pdf_path or None)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / safe_filename(record["id"], ".pdf")
    doi = (record.get("doi") or "").strip()

    # A file may already sit here because it was placed manually (see Step 5's
    # manual-download checkpoint in instructions.md) — trust it without a
    # network call if it's a real PDF, so a plain re-run after manual
    # downloads picks it up automatically.
    if dest.exists() and dest.stat().st_size > 0:
        with dest.open("rb") as fh:
            if fh.read(5) == b"%PDF-":
                return "downloaded:manual", str(dest.relative_to(_REPO_ROOT))

    if record.get("pdf_url"):
        body = _try_download(record["pdf_url"])
        if body:
            dest.write_bytes(body)
            return "downloaded:open_access", str(dest.relative_to(_REPO_ROOT))

    if doi:
        unpaywall_url = _unpaywall_pdf_url(doi, email=_email())
        if unpaywall_url:
            body = _try_download(unpaywall_url)
            if body:
                dest.write_bytes(body)
                return "downloaded:open_access", str(dest.relative_to(_REPO_ROOT))

    if doi:
        institute_url = _citation_pdf_url_from_landing_page(doi)
        if institute_url:
            body = _try_download(institute_url)
            if body:
                dest.write_bytes(body)
                return "downloaded:institute", str(dest.relative_to(_REPO_ROOT))

    return "unavailable", None


def _email() -> str:
    import os

    return os.getenv("OPENALEX_EMAIL") or os.getenv("NCBI_EMAIL") or "literature-survey@example.org"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--question", required=True)
    ap.add_argument("--slug", default=None)
    ap.add_argument("--store-path", default=str(DEFAULT_STORE_PATH))
    ap.add_argument("--limit", type=int, default=None, help="Only attempt the first N missing papers (for testing)")
    ap.add_argument("--force", action="store_true",
                     help="Re-attempt every paper, including ones already downloaded. Without this flag, a "
                          "plain re-run only retries papers that are missing or came back 'unavailable'/'error:*' "
                          "last time — which is exactly what you want after placing PDFs manually.")
    args = ap.parse_args()

    store_path = Path(args.store_path)
    store = load(store_path)
    records = [r for r in store.values() if r.get("question") == args.question]
    if not records:
        sys.stderr.write(f"[ERROR] no papers in store for question: {args.question!r}\n")
        sys.exit(1)

    if not args.force:
        records = [
            r for r in records
            if not r.get("pdf_status")
            or r["pdf_status"] == "unavailable"
            or r["pdf_status"].startswith("error:")
        ]
    if args.limit:
        records = records[: args.limit]

    qdir = question_dir(args.question, args.slug, BRAINSTORM_ROOT)
    out_dir = qdir / "pdfs"

    counts = {"downloaded:open_access": 0, "downloaded:institute": 0, "downloaded:manual": 0, "unavailable": 0}
    manual_needed = []

    for i, r in enumerate(records, 1):
        sys.stderr.write(f"[{i}/{len(records)}] {(r.get('title') or '')[:70]}\n")
        try:
            status, path = fetch_one(r, out_dir)
        except Exception as exc:
            status, path = f"error:{exc}", None
            sys.stderr.write(f"[WARN] {exc}\n")
        r["pdf_status"] = status
        if path:
            r["pdf_path"] = path
        counts[status] = counts.get(status, 0) + 1
        if status == "unavailable":
            manual_needed.append(r)

    save(store, store_path)

    if manual_needed:
        lines = [
            f"# Papers needing manual PDF download ({len(manual_needed)})\n",
            "To have these picked up automatically, save the PDF under the exact filename "
            "given below (in this same directory), then re-run `fetch_pdfs.py` without `--force`.\n",
        ]
        for r in manual_needed:
            lines.append(f"- **{r.get('title', '(no title)')}**")
            lines.append(f"  DOI: {r.get('doi') or 'n/a'} | URL: {r.get('url') or 'n/a'}")
            lines.append(f"  Save as: `{safe_filename(r['id'], '.pdf')}`")
        (out_dir / "_manual_download_needed.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Attempted {len(records)} papers.")
    for status, count in counts.items():
        print(f"  {status}: {count}")
    print(f"PDFs saved under: {out_dir}")
    if manual_needed:
        print(f"Manual-download list: {out_dir / '_manual_download_needed.md'}")


if __name__ == "__main__":
    main()
