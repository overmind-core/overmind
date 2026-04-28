"""Minimal SEC EDGAR wrapper.

EDGAR is free and key-less but requires a descriptive User-Agent.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_UA = os.environ.get("EDGAR_USER_AGENT", "overclaw-examples contact@example.com")


def _ticker_to_cik(ticker: str) -> str | None:
    try:
        resp = httpx.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        t = ticker.upper()
        for row in data.values():
            if row.get("ticker", "").upper() == t:
                return str(row["cik_str"]).zfill(10)
    except Exception:
        return None
    return None


def _latest_filing(ticker: str, form: str) -> dict[str, Any]:
    cik = _ticker_to_cik(ticker)
    if not cik:
        return {"error": f"could not resolve ticker {ticker}"}
    try:
        resp = httpx.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=20.0,
        )
        resp.raise_for_status()
        sub = resp.json()
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accns = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])
        primary = recent.get("primaryDocument", [])
        for i, f in enumerate(forms):
            if f == form:
                accn = accns[i].replace("-", "")
                doc = primary[i]
                return {
                    "ticker": ticker.upper(),
                    "form": form,
                    "filing_date": dates[i],
                    "accession": accns[i],
                    "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{doc}",
                }
        return {"error": f"no {form} found for {ticker}"}
    except Exception as e:
        return {"error": str(e)}


def sec_edgar_latest_10k(ticker: str) -> dict[str, Any]:
    """Get the latest 10-K filing summary for a ticker."""
    return _latest_filing(ticker, "10-K")


def sec_edgar_latest_10q(ticker: str) -> dict[str, Any]:
    """Get the latest 10-Q filing summary for a ticker."""
    return _latest_filing(ticker, "10-Q")
