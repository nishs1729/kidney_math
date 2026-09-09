"""
search_utils.py — Unified Literature Search Utilities.

Single-file, zero-dependency module for querying PubMed, Semantic Scholar,
and bioRxiv/medRxiv. All functions return a consistent article schema so
results from different sources can be mixed, deduplicated, and processed
uniformly in a search loop.

Unified Article Schema
----------------------
Each article dict contains (empty string/list/0 when unavailable):

  source          str        'pubmed' | 'semantic_scholar' | 'biorxiv' | 'medrxiv'
  title           str
  authors         list[str]
  year            str        e.g. '2023'
  pub_date        str        e.g. '2023-05-12' or 'May 2023'
  venue           str        journal / conference / preprint server name
  abstract        str
  doi             str
  url             str        canonical URL for the article
  pdf_url         str        open-access PDF if available, else ''
  ids             dict       source-specific IDs: pmid, arxiv_id, paper_id, …
  citation_count  int        (Semantic Scholar only; 0 for others)
  keywords        list[str]  MeSH terms (PubMed) or fields-of-study (S2)
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
        item: Dict[str, Any] = {
            "pmid": pmid, "title": title, "authors": authors,
            "journal": journal, "year": date["year"], "pub_date": date["pub_date"],
            "doi": doi, "pmc": pmc,
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
# Semantic Scholar  (S2AG)
# ============================================================================

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_S2_FIELDS = (
    "paperId,externalIds,title,abstract,authors,year,publicationDate,"
    "venue,publicationVenue,referenceCount,citationCount,isOpenAccess,"
    "openAccessPdf,fieldsOfStudy"
)
_S2_FIELDS_COMPACT = (
    "paperId,externalIds,title,authors,year,venue,citationCount,isOpenAccess,openAccessPdf"
)


def _s2_parse(raw: Dict[str, Any]) -> Dict[str, Any]:
    ext = raw.get("externalIds") or {}
    pid = raw.get("paperId", "")
    pv = raw.get("publicationVenue") or {}
    pdf = (raw.get("openAccessPdf") or {}).get("url", "")
    return {
        "paper_id": pid,
        "title": raw.get("title", ""),
        "authors": [a["name"] for a in (raw.get("authors") or []) if a.get("name")],
        "year": str(raw.get("year") or ""),
        "pub_date": raw.get("publicationDate") or "",
        "venue": pv.get("name") or raw.get("venue") or "",
        "citation_count": raw.get("citationCount") or 0,
        "reference_count": raw.get("referenceCount") or 0,
        "doi": ext.get("DOI", ""),
        "arxiv_id": ext.get("ArXiv", ""),
        "pmid": ext.get("PubMed", ""),
        "is_open_access": bool(raw.get("isOpenAccess")),
        "open_access_pdf": pdf,
        "fields_of_study": raw.get("fieldsOfStudy") or [],
        "abstract": raw.get("abstract") or "",
        "url": f"https://www.semanticscholar.org/paper/{pid}" if pid else "",
    }


def _s2_headers(api_key: Optional[str] = None) -> Dict[str, str]:
    key = api_key or os.getenv("S2_API_KEY")
    h = {"Accept": "application/json"}
    if key:
        h["x-api-key"] = key
    return h


def _s2_search_raw(
    query: str,
    max_results: int = 10,
    year_range: Optional[str] = None,
    fields_of_study: Optional[List[str]] = None,
    compact: bool = False,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "query": query,
        "limit": min(max_results, 100),
        "fields": _S2_FIELDS_COMPACT if compact else _S2_FIELDS,
    }
    if fields_of_study:
        params["fieldsOfStudy"] = ",".join(fields_of_study)
    if year_range:
        params["year"] = year_range
    qs = urllib.parse.urlencode(params)
    raw = _http_get(f"{_S2_BASE}/paper/search?{qs}", headers=_s2_headers(api_key))
    data = json.loads(raw)
    papers = [_s2_parse(p) for p in (data.get("data") or [])]
    return {"total": data.get("total", 0), "papers": papers}


def _s2_batch_raw(
    paper_ids: List[str],
    compact: bool = False,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    fields = _S2_FIELDS_COMPACT if compact else _S2_FIELDS
    qs = urllib.parse.urlencode({"fields": fields})
    body = json.dumps({"ids": paper_ids}).encode()
    raw = _http_post(
        f"{_S2_BASE}/paper/batch?{qs}",
        body=body,
        extra_headers=_s2_headers(api_key),
    )
    return [_s2_parse(p) for p in json.loads(raw) if p]


# ============================================================================
# bioRxiv / medRxiv
# ============================================================================

_BIO_BASE = "https://api.biorxiv.org"
_MED_BASE = "https://api.medrxiv.org"
_DEFAULT_DATE_RANGE = "2019-01-01:2099-12-31"


def _rxiv_base(server: str) -> str:
    return _MED_BASE if server == "medrxiv" else _BIO_BASE


def _rxiv_parse(raw: Dict[str, Any], server: str) -> Dict[str, Any]:
    doi = raw.get("doi", "")
    authors_raw = raw.get("authors", "")
    authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
    return {
        "doi": doi,
        "title": raw.get("title", ""),
        "authors": authors,
        "date": raw.get("date", ""),
        "year": (raw.get("date") or "")[:4],
        "server": server,
        "category": raw.get("category", ""),
        "version": raw.get("version", ""),
        "abstract": raw.get("abstract", ""),
        "url": f"https://www.{server}.org/content/{doi}" if doi else "",
        "published": raw.get("published", ""),
    }


def _rxiv_search_raw(
    query: str,
    server: str = "biorxiv",
    max_results: int = 10,
    date_range: str = _DEFAULT_DATE_RANGE,
) -> Dict[str, Any]:
    base = _rxiv_base(server)
    papers: List[Dict[str, Any]] = []
    total = 0
    cursor = 0
    encoded = urllib.parse.quote(query)
    while len(papers) < max_results:
        url = f"{base}/search/{server}/{encoded}/{date_range}/{cursor}/json"
        try:
            raw = _http_get(url)
        except Exception as exc:
            sys.stderr.write(
                f"[WARN] {server} search unavailable ({exc}); "
                "falling back to date-browse + local filter.\n"
            )
            # Fallback: date-range browse filtered locally
            browse_url = f"{base}/details/{server}/{date_range}/0/json"
            try:
                raw2 = _http_get(browse_url)
                data2 = json.loads(raw2)
                total = int((data2.get("messages", [{}])[0]).get("total", 0))
                q_lower = query.lower()
                for item in data2.get("collection") or []:
                    if len(papers) >= max_results:
                        break
                    text = (item.get("title", "") + " " + item.get("abstract", "")).lower()
                    if all(t in text for t in q_lower.split()):
                        papers.append(_rxiv_parse(item, server))
            except Exception as exc2:
                sys.stderr.write(f"[WARN] {server} fallback also failed: {exc2}\n")
            break
        data = json.loads(raw)
        msgs = data.get("messages", [{}])
        total = int((msgs[0] if msgs else {}).get("total", 0))
        coll = data.get("collection") or []
        for item in coll:
            if len(papers) >= max_results:
                break
            papers.append(_rxiv_parse(item, server))
        if len(coll) < 100:
            break
        cursor += 100
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
        "is_preprint": False,
    }


def _norm_s2(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source": "semantic_scholar",
        "title": a.get("title", ""),
        "authors": a.get("authors", []),
        "year": str(a.get("year", "")),
        "pub_date": a.get("pub_date", ""),
        "venue": a.get("venue", ""),
        "abstract": a.get("abstract", ""),
        "doi": a.get("doi", ""),
        "url": a.get("url", ""),
        "pdf_url": a.get("open_access_pdf", ""),
        "ids": {
            "paper_id": a.get("paper_id", ""),
            "doi": a.get("doi", ""),
            "arxiv_id": a.get("arxiv_id", ""),
            "pmid": a.get("pmid", ""),
        },
        "citation_count": a.get("citation_count", 0),
        "keywords": a.get("fields_of_study", []),
        "is_preprint": False,
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
        "pdf_url": "",
        "ids": {
            "doi": a.get("doi", ""),
            "version": a.get("version", ""),
            "published_doi": a.get("published", ""),
        },
        "citation_count": 0,
        "keywords": [a["category"]] if a.get("category") else [],
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


def search_semantic_scholar(
    query: str,
    max_results: int = 10,
    year_range: Optional[str] = None,
    fields_of_study: Optional[List[str]] = None,
    compact: bool = False,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Search Semantic Scholar.

    Args:
        query: Free-text search string.
        max_results: Max articles (up to 100 per request).
        year_range: e.g. '2018-2024' or '2020'.
        fields_of_study: e.g. ['Medicine', 'Biology'].
        compact: Omit abstracts/fields-of-study.
        api_key: S2 API key or set S2_API_KEY env var.

    Returns:
        List of normalised article dicts.
    """
    res = _s2_search_raw(query, max_results, year_range, fields_of_study, compact, api_key)
    arts = res["papers"]
    if compact:
        for a in arts:
            a.pop("abstract", None)
            a.pop("fields_of_study", None)
    return [_norm_s2(a) for a in arts]


def fetch_s2_by_ids(
    paper_ids: List[str],
    compact: bool = False,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Fetch Semantic Scholar papers by ID.

    IDs may be: DOI:10.x/y  ARXIV:1234.56789  CorpusId:12345  or raw S2 hex.
    Returns normalised article dicts.
    """
    raw = _s2_batch_raw(paper_ids, compact, api_key)
    if compact:
        for a in raw:
            a.pop("abstract", None)
            a.pop("fields_of_study", None)
    return [_norm_s2(a) for a in raw]


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
    s2_api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Query multiple sources and return a combined, deduplicated list.

    Args:
        query: Search terms used across all sources.
        max_results: Max articles *per source*.
        sources: Which sources to query. Defaults to all four:
                 ['pubmed', 'semantic_scholar', 'biorxiv', 'medrxiv'].
        compact: Omit abstracts from all results (good for broad sweeps).
        year_range: Year filter for Semantic Scholar, e.g. '2018-2024'.
        date_range: Date filter for preprint servers.
        pubmed_api_key: NCBI API key (or NCBI_API_KEY env var).
        pubmed_email: NCBI email (or NCBI_EMAIL env var).
        s2_api_key: Semantic Scholar API key (or S2_API_KEY env var).

    Returns:
        Deduplicated list of normalised article dicts.
    """
    if sources is None:
        sources = ["pubmed", "semantic_scholar", "biorxiv", "medrxiv"]

    all_articles: List[Dict[str, Any]] = []
    _dispatch = {
        "pubmed": lambda: search_pubmed(query, max_results, compact=compact,
                                         api_key=pubmed_api_key, email=pubmed_email),
        "semantic_scholar": lambda: search_semantic_scholar(query, max_results,
                                                             year_range=year_range,
                                                             compact=compact,
                                                             api_key=s2_api_key),
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
# Deduplication & helpers
# ============================================================================

def deduplicate(
    articles: List[Dict[str, Any]],
    prefer_sources: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Deduplicate normalised article dicts across sources.

    Strategy (in priority order):
      1. Exact DOI match (lowercase-normalised).
      2. Title match: alpha-only characters compared (handles punctuation variants).

    When duplicates are found the copy from the higher-preference source is kept.
    Default preference: pubmed > semantic_scholar > biorxiv > medrxiv.

    Args:
        articles: Mixed list of normalised article dicts.
        prefer_sources: Source preference for keeping duplicates.

    Returns:
        Deduplicated list preserving first-occurrence order.
    """
    if prefer_sources is None:
        prefer_sources = ["pubmed", "semantic_scholar", "biorxiv", "medrxiv"]
    rank = {s: i for i, s in enumerate(prefer_sources)}

    seen_doi: Dict[str, int] = {}
    seen_title: Dict[str, int] = {}
    unique: List[Dict[str, Any]] = []

    for art in articles:
        doi_key = art.get("doi", "").strip().lower()
        title_key = "".join(c for c in art.get("title", "").lower() if c.isalpha())

        idx: Optional[int] = None
        if doi_key:
            idx = seen_doi.get(doi_key)
        if idx is None and title_key:
            idx = seen_title.get(title_key)

        if idx is None:
            i = len(unique)
            unique.append(art)
            if doi_key:
                seen_doi[doi_key] = i
            if title_key:
                seen_title[title_key] = i
        else:
            # Keep the preferred-source version
            old_rank = rank.get(unique[idx].get("source", ""), 999)
            new_rank = rank.get(art.get("source", ""), 999)
            if new_rank < old_rank:
                unique[idx] = art
                if doi_key:
                    seen_doi[doi_key] = idx
                if title_key:
                    seen_title[title_key] = idx

    return unique


def extract_dois(articles: List[Dict[str, Any]]) -> List[str]:
    """Return non-empty DOIs from a normalised article list."""
    return [a["doi"] for a in articles if a.get("doi")]


def extract_titles(articles: List[Dict[str, Any]]) -> List[str]:
    """Return titles from a normalised article list."""
    return [a["title"] for a in articles if a.get("title")]
