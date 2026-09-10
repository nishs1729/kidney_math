"""
search_utils.py — Unified Literature Search Utilities.

Single-file, zero-dependency module for querying PubMed, OpenAlex,
and bioRxiv/medRxiv. All functions return a consistent article schema so
results from different sources can be mixed, deduplicated, and processed
uniformly in a search loop.

Unified Article Schema
----------------------
Each article dict contains (empty string/list/0 when unavailable):

  source          str        'pubmed' | 'openalex' | 'biorxiv' | 'medrxiv'
  title           str
  authors         list[str]
  year            str        e.g. '2023'
  pub_date        str        e.g. '2023-05-12' or 'May 2023'
  venue           str        journal / conference / preprint server name
  abstract        str
  doi             str
  url             str        canonical URL for the article
  pdf_url         str        open-access PDF if available, else ''
  ids             dict       source-specific IDs: pmid, openalex_id, …
  citation_count  int        (OpenAlex only; 0 for others)
  keywords        list[str]  MeSH terms (PubMed) or concepts (OpenAlex)
  pub_types       list[str]  publication types (PubMed/Europe PMC only; [] for OpenAlex)
  is_review       bool       True if any pub_type mentions "review" (PubMed/Europe PMC only)
  is_preprint     bool

Quick usage
-----------
  from tool.search_utils import search_all, deduplicate

  hits   = search_all("SGLT2 kidney tubule", max_results=10)
  unique = deduplicate(hits)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Ensure repo root on sys.path (mirrors pubmed_query.py convention)
# ---------------------------------------------------------------------------
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_USER_AGENT = "LiteratureSurveyTool/1.0 (agent-literature-survey)"

# ============================================================================
# Shared HTTP helper
# ============================================================================

def _http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    max_retries: int = 3,
    backoff: float = 1.0,
) -> bytes:
    """GET with exponential backoff on 429 / 5xx."""
    hdrs = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                t = backoff * (2 ** attempt)
                sys.stderr.write(f"[WARN] HTTP {e.code} — retrying in {t:.1f}s\n")
                time.sleep(t)
            else:
                body = e.read().decode("utf-8", errors="replace")
                sys.stderr.write(f"[ERROR] GET {url}: {e} — {body}\n")
                raise
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                t = backoff * (2 ** attempt)
                sys.stderr.write(f"[WARN] URLError — retrying in {t:.1f}s\n")
                time.sleep(t)
            else:
                sys.stderr.write(f"[ERROR] Network error: {e}\n")
                raise
    raise RuntimeError(f"Failed after {max_retries} retries: {url}")


def _http_post(
    url: str,
    body: bytes,
    extra_headers: Optional[Dict[str, str]] = None,
    max_retries: int = 3,
    backoff: float = 1.0,
) -> bytes:
    """POST with exponential backoff."""
    hdrs = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if extra_headers:
        hdrs.update(extra_headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                t = backoff * (2 ** attempt)
                sys.stderr.write(f"[WARN] HTTP {e.code} — retrying in {t:.1f}s\n")
                time.sleep(t)
            else:
                body_err = e.read().decode("utf-8", errors="replace")
                sys.stderr.write(f"[ERROR] POST {url}: {e} — {body_err}\n")
                raise
        except urllib.error.URLError as e:
            if attempt < max_retries - 1:
                t = backoff * (2 ** attempt)
                sys.stderr.write(f"[WARN] URLError — retrying in {t:.1f}s\n")
                time.sleep(t)
            else:
                raise
    raise RuntimeError(f"POST failed after {max_retries} retries: {url}")


# ============================================================================
# PubMed  (NCBI E-utilities)
# ============================================================================

_NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_NCBI_TOOL = "ai_literature_survey_agent"


def _pm_clean(elem: Optional[ET.Element]) -> str:
    if elem is None:
        return ""
    return " ".join("".join(elem.itertext()).split()).strip()


def _pm_abstract(article: ET.Element) -> str:
    ab = article.find(".//Abstract")
    if ab is None:
        return ""
    els = ab.findall("AbstractText")
    if not els:
        return _pm_clean(ab)
    structured = any(el.get("Label") or el.get("NlmCategory") for el in els)
    parts = []
    for el in els:
        label = el.get("Label") or el.get("NlmCategory")
        text = _pm_clean(el)
        if not text:
            continue
        parts.append(f"{label.upper()}: {text}" if structured and label else text)
    return "\n\n".join(parts)


def _pm_authors(article: ET.Element) -> List[str]:
    out = []
    for a in article.findall(".//AuthorList/Author"):
        coll = _pm_clean(a.find("CollectiveName"))
        if coll:
            out.append(coll)
            continue
        last = _pm_clean(a.find("LastName"))
        init = _pm_clean(a.find("Initials")) or _pm_clean(a.find("ForeName"))
        if last:
            out.append(f"{last} {init}".strip())
    return out


def _pm_year(article: ET.Element) -> Dict[str, str]:
    pd = article.find(".//JournalIssue/PubDate")
    parts, year = [], ""
    if pd is not None:
        y = pd.find("Year")
        if y is not None:
            year = _pm_clean(y)
            parts.append(year)
        m = _pm_clean(pd.find("Month"))
        if m:
            parts.append(m)
        d = _pm_clean(pd.find("Day"))
        if d:
            parts.append(d)
        if not year:
            ml = _pm_clean(pd.find("MedlineDate"))
            if ml:
                parts.append(ml)
                for tok in ml.split():
                    if len(tok) == 4 and tok.isdigit():
                        year = tok
                        break
    if not year:
        dc = article.find(".//DateCompleted/Year")
        if dc is not None:
            year = _pm_clean(dc)
    return {"year": year, "pub_date": " ".join(parts) if parts else year}


def _pm_fetch_raw(
    pmids: List[str],
    include_abstract: bool = True,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": _NCBI_TOOL,
        "email": email or os.getenv("NCBI_EMAIL"),
        "api_key": api_key or os.getenv("NCBI_API_KEY"),
    }
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    raw = _http_get(f"{_NCBI_BASE}/efetch.fcgi?{qs}")
    root = ET.fromstring(raw)
    results = []
    for node in root.findall(".//PubmedArticle"):
        med = node.find("MedlineCitation")
        if med is None:
            continue
        pmid = _pm_clean(med.find("PMID"))
        art = med.find("Article") or ET.Element("Article")
        title = _pm_clean(art.find("ArticleTitle"))
        journal_iso = _pm_clean(art.find(".//Journal/ISOAbbreviation"))
        journal = journal_iso or _pm_clean(art.find(".//Journal/Title"))
        date = _pm_year(art)
        authors = _pm_authors(art)
        doi = pmc = ""
        for aid in node.findall(".//PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi" and not doi:
                doi = _pm_clean(aid)
            elif aid.get("IdType") == "pmc" and not pmc:
                pmc = _pm_clean(aid)
        if not doi:
            for el in art.findall(".//ELocationID"):
                if el.get("EIdType") == "doi":
                    doi = _pm_clean(el)
                    break
        pub_types = [
            _pm_clean(p) for p in art.findall(".//PublicationTypeList/PublicationType")
            if _pm_clean(p)
        ]
        item: Dict[str, Any] = {
            "pmid": pmid, "title": title, "authors": authors,
            "journal": journal, "year": date["year"], "pub_date": date["pub_date"],
            "doi": doi, "pmc": pmc, "pub_types": pub_types,
            "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "abstract": _pm_abstract(art) if include_abstract else "",
        }
        if include_abstract:
            item["mesh_terms"] = [
                _pm_clean(m) for m in med.findall(".//MeshHeadingList/MeshHeading/DescriptorName")
                if _pm_clean(m)
            ]
        else:
            item["mesh_terms"] = []
        results.append(item)
    return results


def _pm_search_ids(
    query: str,
    max_results: int = 10,
    sort: str = "relevance",
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    params = {
        "db": "pubmed", "term": query, "retmode": "json",
        "retmax": max_results, "sort": sort,
        "tool": _NCBI_TOOL,
        "email": email or os.getenv("NCBI_EMAIL"),
        "api_key": api_key or os.getenv("NCBI_API_KEY"),
    }
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    raw = _http_get(f"{_NCBI_BASE}/esearch.fcgi?{qs}")
    data = json.loads(raw)["esearchresult"]
    return {"count": int(data.get("count", 0)), "pmids": data.get("idlist", [])}


# ============================================================================
# OpenAlex
#
# Replaces Semantic Scholar (removed): S2's unauthenticated tier was
# persistently rate-limited (HTTP 429) with no key available. OpenAlex is a
# free, keyless alternative (a `mailto` contact param buys the "polite pool"
# rate tier) that still gives citation counts, referenced/citing work ids for
# future citation-graph work, and concept tags.
# ============================================================================

_OA_BASE = "https://api.openalex.org/works"


def _oa_reconstruct_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """OpenAlex returns abstracts as a word -> [positions] inverted index; rebuild plain text."""
    if not inverted_index:
        return ""
    positions: List[tuple] = []
    for word, idxs in inverted_index.items():
        for idx in idxs:
            positions.append((idx, word))
    positions.sort(key=lambda p: p[0])
    return " ".join(w for _, w in positions)


def _oa_parse(raw: Dict[str, Any], compact: bool = False) -> Dict[str, Any]:
    oa_id = (raw.get("id") or "").rsplit("/", 1)[-1]
    doi_url = raw.get("doi") or ""
    doi = doi_url.split("doi.org/", 1)[-1] if doi_url else ""
    pmid_url = (raw.get("ids") or {}).get("pmid", "")
    pmid = pmid_url.rsplit("/", 1)[-1] if pmid_url else ""

    authors = [
        (au.get("author") or {}).get("display_name", "")
        for au in (raw.get("authorships") or [])
    ]
    authors = [a for a in authors if a]

    primary_loc = raw.get("primary_location") or {}
    source = primary_loc.get("source") or {}
    oa = raw.get("open_access") or {}
    pdf_url = primary_loc.get("pdf_url") or oa.get("oa_url") or ""

    concepts = [c["display_name"] for c in (raw.get("concepts") or []) if c.get("display_name")]

    return {
        "openalex_id": oa_id,
        "title": raw.get("title", "") or "",
        "authors": authors,
        "year": raw.get("publication_year") or "",
        "pub_date": raw.get("publication_date", "") or "",
        "venue": source.get("display_name", "") or "",
        "citation_count": raw.get("cited_by_count") or 0,
        "doi": doi,
        "pmid": pmid,
        "url": doi_url or (raw.get("id") or ""),
        "pdf_url": pdf_url,
        "concepts": concepts,
        "is_preprint": (raw.get("type") or "") == "preprint",
        "abstract": "" if compact else _oa_reconstruct_abstract(raw.get("abstract_inverted_index")),
    }


def _oa_search_raw(
    query: str,
    max_results: int = 10,
    year_range: Optional[str] = None,
    compact: bool = False,
    mailto: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "search": query,
        "per-page": min(max_results, 100),
        "mailto": mailto or os.getenv("OPENALEX_EMAIL"),
    }
    if year_range:
        # Accept '2018-2024' or a single year; OpenAlex wants a from/to filter.
        if "-" in year_range:
            start, end = year_range.split("-", 1)
            params["filter"] = f"from_publication_date:{start}-01-01,to_publication_date:{end}-12-31"
        else:
            params["filter"] = f"publication_year:{year_range}"
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    raw = _http_get(f"{_OA_BASE}?{qs}")
    data = json.loads(raw)
    papers = [_oa_parse(p, compact) for p in (data.get("results") or [])]
    return {"total": (data.get("meta") or {}).get("count", 0), "papers": papers}


def _oa_batch_raw(
    dois: List[str],
    compact: bool = False,
    mailto: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not dois:
        return []
    results: List[Dict[str, Any]] = []
    for i in range(0, len(dois), 50):
        chunk = dois[i:i + 50]
        filt = "doi:" + "|".join(urllib.parse.quote(d, safe="") for d in chunk)
        params = {"filter": filt, "per-page": len(chunk), "mailto": mailto or os.getenv("OPENALEX_EMAIL")}
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
        raw = _http_get(f"{_OA_BASE}?{qs}")
        data = json.loads(raw)
        results.extend(_oa_parse(p, compact) for p in (data.get("results") or []))
    return results


# ============================================================================
# bioRxiv / medRxiv
#
# api.biorxiv.org / api.medrxiv.org never offered a real keyword-search
# endpoint for general use (only date-range "details" dumps); the old
# /search/{server}/{terms}/... path this module used to call has since
# started 404-ing outright. Europe PMC indexes both preprint servers (as
# source "PPR") behind a proper full-text search API with no key required,
# so that's what we query instead, filtered to the requesting server via
# PUBLISHER:"bioRxiv" / PUBLISHER:"medRxiv".
# ============================================================================

_EPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_DEFAULT_DATE_RANGE = "2019-01-01:2099-12-31"


_epmc_last_request: float = 0.0  # monotonic timestamp of last Europe PMC request


def _rxiv_throttle(min_interval: float = 0.34) -> None:
    """Sleep if needed to keep Europe PMC requests to a courteous rate."""
    global _epmc_last_request
    elapsed = time.monotonic() - _epmc_last_request
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _epmc_last_request = time.monotonic()


def _epmc_date_filter(date_range: str) -> str:
    """Convert 'YYYY-MM-DD:YYYY-MM-DD' into an EPMC FIRST_PDATE range clause."""
    try:
        start, end = date_range.split(":")
    except ValueError:
        return ""
    return f' AND FIRST_PDATE:[{start} TO {end}]'


def _rxiv_parse(raw: Dict[str, Any], server: str) -> Dict[str, Any]:
    doi = raw.get("doi", "")
    authors = [a.get("fullName", "") for a in
               (raw.get("authorList") or {}).get("author", []) if a.get("fullName")]
    if not authors:
        authors = [a.strip() for a in (raw.get("authorString") or "").split(",") if a.strip()]
    pub_date = raw.get("firstPublicationDate", "") or ""
    year = raw.get("pubYear", "") or pub_date[:4]

    published = ""
    for cc in (raw.get("commentCorrectionList") or {}).get("commentCorrection", []):
        if cc.get("type") == "Preprint of":
            published = cc.get("reference", "")
            break

    pdf_url = ""
    for link in (raw.get("fullTextUrlList") or {}).get("fullTextUrl", []):
        if link.get("documentStyle") == "pdf":
            pdf_url = link.get("url", "")
            break

    url = f"https://doi.org/{doi}" if doi else ""
    pub_types = (raw.get("pubTypeList") or {}).get("pubType", [])

    return {
        "doi": doi,
        "title": raw.get("title", ""),
        "authors": authors,
        "date": pub_date,
        "year": year,
        "server": server,
        "category": "",
        "version": "",
        "abstract": raw.get("abstractText", ""),
        "url": url,
        "pdf_url": pdf_url,
        "pub_types": pub_types,
        "published": published,
    }


def _rxiv_search_raw(
    query: str,
    server: str = "biorxiv",
    max_results: int = 10,
    date_range: str = _DEFAULT_DATE_RANGE,
) -> Dict[str, Any]:
    publisher = "medRxiv" if server == "medrxiv" else "bioRxiv"
    epmc_query = f'{query} AND SRC:PPR AND PUBLISHER:"{publisher}"' + _epmc_date_filter(date_range)

    papers: List[Dict[str, Any]] = []
    total = 0
    cursor_mark = "*"
    page_size = min(max_results, 100)

    while len(papers) < max_results:
        params = {
            "query": epmc_query,
            "format": "json",
            "resultType": "core",
            "pageSize": page_size,
            "cursorMark": cursor_mark,
        }
        qs = urllib.parse.urlencode(params)
        try:
            _rxiv_throttle()
            raw = _http_get(f"{_EPMC_BASE}/search?{qs}")
        except Exception as exc:
            sys.stderr.write(f"[WARN] {server} (Europe PMC) search failed: {exc}\n")
            break

        data = json.loads(raw)
        total = int(data.get("hitCount", 0))
        results = (data.get("resultList") or {}).get("result", [])
        for item in results:
            if len(papers) >= max_results:
                break
            papers.append(_rxiv_parse(item, server))

        next_cursor = data.get("nextCursorMark", "")
        if not results or not next_cursor or next_cursor == cursor_mark:
            break
        cursor_mark = next_cursor

    return {"total": total, "papers": papers}


# ============================================================================
# Schema normalisation
# ============================================================================

def _norm_pubmed(a: Dict[str, Any]) -> Dict[str, Any]:
    pmid = a.get("pmid", "")
    return {
        "source": "pubmed",
        "title": a.get("title", ""),
        "authors": a.get("authors", []),
        "year": a.get("year", ""),
        "pub_date": a.get("pub_date", ""),
        "venue": a.get("journal", ""),
        "abstract": a.get("abstract", ""),
        "doi": a.get("doi", ""),
        "url": a.get("pubmed_url", f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"),
        "pdf_url": "",
        "ids": {"pmid": pmid, "pmc": a.get("pmc", ""), "doi": a.get("doi", "")},
        "citation_count": 0,
        "keywords": a.get("mesh_terms", []),
        "pub_types": a.get("pub_types", []),
        "is_review": any("review" in t.lower() for t in a.get("pub_types", [])),
        "is_preprint": False,
    }


def _norm_openalex(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "openalex",
        "title": a.get("title", ""),
        "authors": a.get("authors", []),
        "year": str(a.get("year", "")),
        "pub_date": a.get("pub_date", ""),
        "venue": a.get("venue", ""),
        "abstract": a.get("abstract", ""),
        "doi": a.get("doi", ""),
        "url": a.get("url", ""),
        "pdf_url": a.get("pdf_url", ""),
        "ids": {
            "openalex_id": a.get("openalex_id", ""),
            "doi": a.get("doi", ""),
            "pmid": a.get("pmid", ""),
        },
        "citation_count": a.get("citation_count", 0),
        "keywords": a.get("concepts", []),
        # OpenAlex's `type` field is too coarse (mostly just "article") to reliably
        # signal review vs. original research; rely on PubMed/Europe PMC for that.
        "pub_types": [],
        "is_review": False,
        "is_preprint": a.get("is_preprint", False),
    }


def _norm_rxiv(a: Dict[str, Any]) -> Dict[str, Any]:
    server = a.get("server", "biorxiv")
    return {
        "source": server,
        "title": a.get("title", ""),
        "authors": a.get("authors", []),
        "year": a.get("year", ""),
        "pub_date": a.get("date", ""),
        "venue": f"{server} preprint",
        "abstract": a.get("abstract", ""),
        "doi": a.get("doi", ""),
        "url": a.get("url", ""),
        "pdf_url": a.get("pdf_url", ""),
        "ids": {
            "doi": a.get("doi", ""),
            "version": a.get("version", ""),
            "published_doi": a.get("published", ""),
        },
        "citation_count": 0,
        "keywords": [a["category"]] if a.get("category") else [],
        "pub_types": a.get("pub_types", []),
        "is_review": any("review" in t.lower() for t in a.get("pub_types", [])),
        "is_preprint": not bool(a.get("published", "")),
    }


# ============================================================================
# Public API — per-source search functions
# ============================================================================

def search_pubmed(
    query: str,
    max_results: int = 10,
    sort: str = "relevance",
    compact: bool = False,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search PubMed. Supports full PubMed query syntax and MeSH terms.

    Args:
        query: Search terms (PubMed syntax OK, e.g. 'SGLT2[MeSH] AND kidney').
        max_results: Max articles (default 10).
        sort: 'relevance' (default) or 'pub_date'.
        compact: Omit abstracts/MeSH to save tokens.
        api_key: NCBI API key or set NCBI_API_KEY env var.
        email: Contact email or set NCBI_EMAIL env var.

    Returns:
        List of normalised article dicts.
    """
    ids = _pm_search_ids(query, max_results, sort, api_key, email)
    raw = _pm_fetch_raw(ids["pmids"], not compact, api_key, email)
    return [_norm_pubmed(a) for a in raw]


def fetch_pubmed_by_pmids(
    pmids: List[str],
    compact: bool = False,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch PubMed articles by PMID list. Returns normalised dicts."""
    raw = _pm_fetch_raw(pmids, not compact, api_key, email)
    return [_norm_pubmed(a) for a in raw]


def search_openalex(
    query: str,
    max_results: int = 10,
    year_range: Optional[str] = None,
    compact: bool = False,
    mailto: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search OpenAlex.

    Note: OpenAlex's `search` parameter does relevance-ranked full-text matching
    on the whole query string — it does not parse PubMed/Europe-PMC-style
    boolean syntax (quotes still work as exact phrases, but AND/OR/parentheses
    are not honored as operators). Complex boolean queries built for the other
    sources will still work here, just with somewhat looser precision.

    Args:
        query: Free-text search string.
        max_results: Max articles (up to 100 per request).
        year_range: e.g. '2018-2024' or '2020'.
        compact: Omit abstracts.
        mailto: Contact email for OpenAlex's "polite pool" (or set OPENALEX_EMAIL).

    Returns:
        List of normalised article dicts.
    """
    res = _oa_search_raw(query, max_results, year_range, compact, mailto)
    return [_norm_openalex(a) for a in res["papers"]]


def fetch_openalex_by_dois(
    dois: List[str],
    compact: bool = False,
    mailto: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch OpenAlex works by DOI list. Returns normalised article dicts."""
    raw = _oa_batch_raw(dois, compact, mailto)
    return [_norm_openalex(a) for a in raw]


def search_biorxiv(
    query: str,
    max_results: int = 10,
    date_range: str = _DEFAULT_DATE_RANGE,
    compact: bool = False,
) -> List[Dict[str, Any]]:
    """
    Search bioRxiv preprints.

    Args:
        query: Keyword search string.
        max_results: Max preprints.
        date_range: 'YYYY-MM-DD:YYYY-MM-DD' (default 2019-present).
        compact: Omit abstracts.

    Returns:
        List of normalised article dicts.
    """
    res = _rxiv_search_raw(query, "biorxiv", max_results, date_range)
    arts = res["papers"]
    if compact:
        for a in arts:
            a.pop("abstract", None)
    return [_norm_rxiv(a) for a in arts]


def search_medrxiv(
    query: str,
    max_results: int = 10,
    date_range: str = _DEFAULT_DATE_RANGE,
    compact: bool = False,
) -> List[Dict[str, Any]]:
    """
    Search medRxiv preprints.

    Args:
        query: Keyword search string.
        max_results: Max preprints.
        date_range: 'YYYY-MM-DD:YYYY-MM-DD' (default 2019-present).
        compact: Omit abstracts.

    Returns:
        List of normalised article dicts.
    """
    res = _rxiv_search_raw(query, "medrxiv", max_results, date_range)
    arts = res["papers"]
    if compact:
        for a in arts:
            a.pop("abstract", None)
    return [_norm_rxiv(a) for a in arts]


# ============================================================================
# Unified multi-source search
# ============================================================================

def search_all(
    query: str,
    max_results: int = 10,
    sources: Optional[List[str]] = None,
    compact: bool = False,
    year_range: Optional[str] = None,
    date_range: str = _DEFAULT_DATE_RANGE,
    pubmed_api_key: Optional[str] = None,
    pubmed_email: Optional[str] = None,
    openalex_mailto: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query multiple sources and return a combined, deduplicated list.

    Args:
        query: Search terms used across all sources.
        max_results: Max articles *per source*.
        sources: Which sources to query. Defaults to all four:
                 ['pubmed', 'openalex', 'biorxiv', 'medrxiv'].
        compact: Omit abstracts from all results (good for broad sweeps).
        year_range: Year filter for OpenAlex, e.g. '2018-2024'.
        date_range: Date filter for preprint servers.
        pubmed_api_key: NCBI API key (or NCBI_API_KEY env var).
        pubmed_email: NCBI email (or NCBI_EMAIL env var).
        openalex_mailto: Contact email for OpenAlex's polite pool (or OPENALEX_EMAIL env var).

    Returns:
        Deduplicated list of normalised article dicts.
    """
    if sources is None:
        sources = ["pubmed", "openalex", "biorxiv", "medrxiv"]

    all_articles: List[Dict[str, Any]] = []
    _dispatch = {
        "pubmed": lambda: search_pubmed(query, max_results, compact=compact,
                                         api_key=pubmed_api_key, email=pubmed_email),
        "openalex": lambda: search_openalex(query, max_results,
                                            year_range=year_range,
                                            compact=compact,
                                            mailto=openalex_mailto),
        "biorxiv": lambda: search_biorxiv(query, max_results, date_range, compact),
        "medrxiv": lambda: search_medrxiv(query, max_results, date_range, compact),
    }

    for src in sources:
        fn = _dispatch.get(src)
        if fn is None:
            sys.stderr.write(f"[WARN] Unknown source '{src}', skipping.\n")
            continue
        try:
            all_articles.extend(fn())
        except Exception as exc:
            sys.stderr.write(f"[WARN] {src} query failed: {exc}\n")

    return deduplicate(all_articles)


# ============================================================================
# Batch multi-query search  (Step 2 of the search loop)
# ============================================================================

def run_batch(
    queries: List[str],
    sources: Optional[List[str]] = None,
    max_per_query: int = 10,
    compact: bool = True,
    query_tags: Optional[Dict[str, str]] = None,
    year_range: Optional[str] = None,
    date_range: str = _DEFAULT_DATE_RANGE,
    rxiv_delay: float = 1.1,
    pubmed_api_key: Optional[str] = None,
    pubmed_email: Optional[str] = None,
    openalex_mailto: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Run a batch of queries across multiple sources with a single dedup pass.

    Designed for Step 2 of the search loop: the LLM generates 8-12 queries
    per round; this function executes them all and returns a flat, deduplicated
    list with provenance attached to each article.

    Each returned article gains two extra fields:
      query_text  str  the query string that retrieved this article
      query_tag   str  optional taxonomy label (from query_tags), else ''

    Args:
        queries: List of search query strings.
        sources: Which sources to query (default: all four).
        max_per_query: Max articles per query per source.
        compact: Omit abstracts (recommended for broad sweeps).
        query_tags: Optional {query: tag} mapping for taxonomy labels.
        year_range: Year filter for OpenAlex.
        date_range: Date filter for bioRxiv/medRxiv.
        rxiv_delay: Seconds between bioRxiv/medRxiv requests (default 1.1).
        pubmed_api_key: NCBI API key (or NCBI_API_KEY env var).
        pubmed_email: NCBI email (or NCBI_EMAIL env var).
        openalex_mailto: Contact email for OpenAlex's polite pool (or OPENALEX_EMAIL env var).

    Returns:
        Flat deduplicated list of normalised article dicts with provenance.
    """
    if sources is None:
        sources = ["pubmed", "openalex", "biorxiv", "medrxiv"]
    if query_tags is None:
        query_tags = {}

    all_articles: List[Dict[str, Any]] = []
    rxiv_sources = {"biorxiv", "medrxiv"}

    for q_idx, query in enumerate(queries):
        tag = query_tags.get(query, "")
        sys.stderr.write(f"[INFO] Batch {q_idx + 1}/{len(queries)}: {query!r}\n")
        for src in sources:
            try:
                if src == "pubmed":
                    arts = search_pubmed(query, max_per_query, compact=compact,
                                         api_key=pubmed_api_key, email=pubmed_email)
                elif src == "openalex":
                    arts = search_openalex(query, max_per_query,
                                           year_range=year_range,
                                           compact=compact,
                                           mailto=openalex_mailto)
                elif src == "biorxiv":
                    arts = search_biorxiv(query, max_per_query, date_range, compact)
                elif src == "medrxiv":
                    arts = search_medrxiv(query, max_per_query, date_range, compact)
                else:
                    sys.stderr.write(f"[WARN] Unknown source '{src}', skipping.\n")
                    continue

                for art in arts:
                    art["query_text"] = query
                    art["query_tag"] = tag
                all_articles.extend(arts)

                # Extra delay for preprint servers beyond the per-request throttle
                if src in rxiv_sources:
                    time.sleep(rxiv_delay)

            except Exception as exc:
                sys.stderr.write(f"[WARN] {src} failed for {query!r}: {exc}\n")

    return deduplicate(all_articles)


# ============================================================================
# Deduplication & helpers
# ============================================================================

def _word_set(title: str) -> set:
    """Lowercase word-set for Jaccard similarity (words longer than 2 chars)."""
    return {w for w in title.lower().split() if len(w) > 2}


def _jaccard(s1: set, s2: set) -> float:
    """Jaccard similarity between two word-sets."""
    if not s1 and not s2:
        return 1.0
    union = len(s1 | s2)
    return len(s1 & s2) / union if union else 0.0


def _author_key(authors: List[str]) -> str:
    """
    Extract the longest alpha token from the first author string (usually the surname).
    Works across source formats: 'Layton AT', 'A. Layton', 'Anita T. Layton'.
    """
    if not authors:
        return ""
    tokens = sorted(
        [t.lower() for t in authors[0].replace(".", " ").split()
         if len(t) > 2 and t.isalpha()],
        key=len,
        reverse=True,
    )
    return tokens[0] if tokens else ""


def deduplicate(
    articles: List[Dict[str, Any]],
    prefer_sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Deduplicate normalised article dicts across sources.

    Matching strategy (first match wins, in priority order):
      1. Exact DOI match (lowercase-normalised).
      2. Alpha-only title key (strips punctuation and case).
      3. Word-Jaccard >= 0.85 on title word-sets.
      4. Word-Jaccard >= 0.50 AND first-author surname match AND year match
         — catches preprint-vs-published pairs with differing subtitles.

    When duplicates are found the copy from the higher-preference source is kept.
    Default preference: pubmed > openalex > biorxiv > medrxiv.

    Args:
        articles: Mixed list of normalised article dicts.
        prefer_sources: Source preference for keeping duplicates.

    Returns:
        Deduplicated list preserving first-occurrence order.
    """
    if prefer_sources is None:
        prefer_sources = ["pubmed", "openalex", "biorxiv", "medrxiv"]
    rank = {s: i for i, s in enumerate(prefer_sources)}

    seen_doi: Dict[str, int] = {}
    seen_title_alpha: Dict[str, int] = {}
    unique: List[Dict[str, Any]] = []
    word_sets: List[set] = []  # parallel to unique for O(n²) Jaccard scan

    def _keep(existing_idx: int, candidate: Dict[str, Any]) -> None:
        """Swap in candidate if it comes from a higher-preference source."""
        existing = unique[existing_idx]
        if rank.get(candidate.get("source", ""), 999) < rank.get(existing.get("source", ""), 999):
            unique[existing_idx] = candidate
            word_sets[existing_idx] = _word_set(candidate.get("title", ""))
            dk = candidate.get("doi", "").strip().lower()
            tk = "".join(c for c in candidate.get("title", "").lower() if c.isalpha())
            if dk:
                seen_doi[dk] = existing_idx
            if tk:
                seen_title_alpha[tk] = existing_idx

    for art in articles:
        doi_key = art.get("doi", "").strip().lower()
        title_alpha = "".join(c for c in art.get("title", "").lower() if c.isalpha())
        ws_new = _word_set(art.get("title", ""))

        # --- Pass 1: exact DOI ---
        if doi_key and doi_key in seen_doi:
            _keep(seen_doi[doi_key], art)
            continue

        # --- Pass 2: alpha-only title ---
        if title_alpha and title_alpha in seen_title_alpha:
            _keep(seen_title_alpha[title_alpha], art)
            continue

        # --- Pass 3 & 4: Jaccard + author/year heuristic ---
        matched_idx: Optional[int] = None
        art_author = _author_key(art.get("authors", []))
        art_year = art.get("year", "")

        for idx, (existing, ws_ex) in enumerate(zip(unique, word_sets)):
            jac = _jaccard(ws_new, ws_ex)
            if jac >= 0.85:
                matched_idx = idx
                break
            if (jac >= 0.50
                    and art_year
                    and art_year == existing.get("year", "")
                    and art_author
                    and art_author == _author_key(existing.get("authors", []))):
                matched_idx = idx
                break

        if matched_idx is not None:
            _keep(matched_idx, art)
            continue

        # --- New article ---
        i = len(unique)
        unique.append(art)
        word_sets.append(ws_new)
        if doi_key:
            seen_doi[doi_key] = i
        if title_alpha:
            seen_title_alpha[title_alpha] = i

    return unique


def extract_dois(articles: List[Dict[str, Any]]) -> List[str]:
    """Return non-empty DOIs from a normalised article list."""
    return [a["doi"] for a in articles if a.get("doi")]


def extract_titles(articles: List[Dict[str, Any]]) -> List[str]:
    """Return titles from a normalised article list."""
    return [a["title"] for a in articles if a.get("title")]


# ============================================================================
# MeSH term lookup
# ============================================================================

def get_mesh_terms(
    query: str,
    retmax: int = 10,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Look up MeSH descriptors matching a free-text concept.

    Uses NCBI E-utilities (esearch + esummary on the 'mesh' database).
    No API key required; set NCBI_API_KEY env var or pass api_key for
    higher rate limits.

    Args:
        query: Free-text concept to look up, e.g. 'tubuloglomerular feedback'.
        retmax: Maximum number of MeSH descriptors to return (default 10).
        api_key: NCBI API key (or set NCBI_API_KEY env var).
        email: Contact email (or set NCBI_EMAIL env var).

    Returns:
        List of dicts with keys:
          mesh_ui    str   MeSH unique identifier
          name       str   preferred descriptor name
          scope_note str   definition / scope note

    Example:
        >>> terms = get_mesh_terms('tubuloglomerular feedback')
        >>> for t in terms:
        ...     print(t['name'], '--', t['scope_note'])
    """
    key = api_key or os.getenv("NCBI_API_KEY")
    mail = email or os.getenv("NCBI_EMAIL")

    common: Dict[str, str] = {}
    if key:
        common["api_key"] = key
    if mail:
        common["email"] = mail

    # 1. Find matching MeSH descriptor UIDs via esearch
    search_params = {
        "db": "mesh",
        "term": query,
        "retmode": "json",
        "retmax": str(retmax),
        "sort": "relevance",
        **common,
    }
    qs = urllib.parse.urlencode(search_params)
    raw = _http_get(f"{_NCBI_BASE}/esearch.fcgi?{qs}")
    search_data = json.loads(raw)
    ids = search_data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    # Rate-limit courtesy pause (3 req/s without API key)
    time.sleep(0.34)

    # 2. Pull descriptor details via esummary
    summary_params = {
        "db": "mesh",
        "id": ",".join(ids),
        "retmode": "json",
        **common,
    }
    qs2 = urllib.parse.urlencode(summary_params)
    raw2 = _http_get(f"{_NCBI_BASE}/esummary.fcgi?{qs2}")
    summary_data = json.loads(raw2)
    result_map = summary_data.get("result", {})

    results = []
    for uid in ids:
        doc = result_map.get(uid, {})
        entry_terms = doc.get("ds_meshterms") or []
        results.append({
            "mesh_ui": doc.get("uid", uid),
            "name": entry_terms[0] if entry_terms else "",
            "entry_terms": entry_terms,   # full list: synonyms, older names, abbreviations
            "scope_note": doc.get("ds_scopenote", ""),
        })
    return results


# ============================================================================
# Concept expansion  (Step 1 of the search loop)
# ============================================================================

def expand_concepts(
    concepts: List[str],
    retmax: int = 5,
    api_key: Optional[str] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Expand a list of free-text concepts into a MeSH-enriched vocabulary dict.

    For each concept, looks up matching MeSH descriptors and returns the
    official name, all entry-terms (synonyms, abbreviations, older terminology),
    and the scope note. Used in Step 1 of the search loop: give the LLM your
    raw concepts, get back the MeSH vocabulary to use when generating queries.

    Args:
        concepts: List of free-text concept strings from the LLM.
        retmax: Max MeSH descriptors to retrieve per concept (default 5).
        api_key: NCBI API key (or set NCBI_API_KEY env var).
        email: Contact email (or set NCBI_EMAIL env var).

    Returns:
        Dict mapping each concept to its MeSH expansion::

          {
            "tubuloglomerular feedback": {
              "status":      "found" | "not_found",
              "mesh_name":   "Tubuloglomerular Feedback",
              "mesh_ui":     "D016548",
              "entry_terms": ["TGF", "tubuloglomerular balance", ...],
              "scope_note":  "...",
              "all_matches": [...]   # full list of MeSH hits (retmax)
            },
            ...
          }
    """
    vocabulary: Dict[str, Any] = {}
    for concept in concepts:
        terms = get_mesh_terms(concept, retmax=retmax, api_key=api_key, email=email)
        if terms:
            best = terms[0]  # highest-ranked MeSH match
            vocabulary[concept] = {
                "status": "found",
                "mesh_name": best["name"],
                "mesh_ui": best["mesh_ui"],
                "entry_terms": best["entry_terms"],
                "scope_note": best["scope_note"],
                "all_matches": terms,
            }
        else:
            vocabulary[concept] = {
                "status": "not_found",
                "mesh_name": "",
                "mesh_ui": "",
                "entry_terms": [],
                "scope_note": "",
                "all_matches": [],
            }
        # Rate-limit courtesy between concept lookups (3 req/s without API key)
        time.sleep(0.34)
    return vocabulary
