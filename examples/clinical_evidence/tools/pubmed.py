"""PubMed E-utilities wrapper.

NCBI E-utilities are free and key-less but ask for an ``email`` or ``tool``
query param for polite usage.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Any

import httpx

_EMAIL = os.environ.get("PUBMED_EMAIL", "overclaw-examples@example.com")
_TOOL = "overclaw-examples"

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def pubmed_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search PubMed and get a list of PMIDs."""
    try:
        resp = httpx.get(
            f"{_BASE}/esearch.fcgi",
            params={
                "db": "pubmed",
                "term": query,
                "retmax": max_results,
                "retmode": "json",
                "sort": "relevance",
                "email": _EMAIL,
                "tool": _TOOL,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "pmids": data.get("esearchresult", {}).get("idlist", []),
            "count": int(data.get("esearchresult", {}).get("count", 0)),
        }
    except Exception as e:
        return {"pmids": [], "error": str(e)}


def pubmed_fetch_abstract(pmid: str) -> dict[str, Any]:
    """Fetch an abstract from PubMed."""
    try:
        resp = httpx.get(
            f"{_BASE}/efetch.fcgi",
            params={
                "db": "pubmed",
                "id": pmid,
                "retmode": "xml",
                "email": _EMAIL,
                "tool": _TOOL,
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        article = root.find(".//PubmedArticle")
        if article is None:
            return {"pmid": pmid, "error": "not found"}
        title_el = article.find(".//ArticleTitle")
        abstract_parts = article.findall(".//Abstract/AbstractText")
        journal_el = article.find(".//Journal/Title")
        year_el = article.find(".//PubDate/Year")
        pub_types = [el.text for el in article.findall(".//PublicationType") if el.text]
        return {
            "pmid": pmid,
            "title": (title_el.text or "") if title_el is not None else "",
            "abstract": " ".join((p.text or "") for p in abstract_parts),
            "journal": (journal_el.text or "") if journal_el is not None else "",
            "year": (year_el.text or "") if year_el is not None else "",
            "publication_types": pub_types,
        }
    except Exception as e:
        return {"pmid": pmid, "error": str(e)}
