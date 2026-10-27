"""
MCP server skeleton for the KYC data.

It exposes ONE working tool, `uk_search_companies`, that calls the mock data
API. Your job (Day 3): extend it so that ALL data the agents use goes through
MCP: the three registers, news, bank systems and document search.

Run locally:      python server.py            (Streamable HTTP on http://localhost:8100/mcp)
Inspect it:       npx @modelcontextprotocol/inspector   then connect to http://localhost:8100/mcp
From the Agent SDK (inside docker compose the URL is http://mcp:8100/mcp):
    options = ClaudeAgentOptions(mcp_servers={"kyc": {"type": "http", "url": settings.mcp_url}})

Tips from 'Writing effective tools for agents':
  * a few tools that match real tasks beat one tool per endpoint
  * namespace tool names (uk_..., pt_..., br_..., news_..., bank_...)
  * return concise, high-signal fields and helpful, actionable errors
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import psycopg
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel
from mcp.server.transport_security import TransportSecuritySettings

from kyc_common.RAG.search import search
from kyc_common.mockdata_client import MockDataClient
from kyc_common.schema import NewsItem
from kyc_common.ownership import RawOwner, normalize_owners, trace_ownership

MOCKDATA_URL = os.getenv("MOCKDATA_URL", "http://localhost:8001").rstrip("/")
# Registry lookup path per country. The PT/BR record carries the people; the UK splits them out.
_LOOKUP = {"UK": "/uk/company/{}", "PT": "/pt/entidade/{}", "BR": "/br/cnpj/{}"}
_PEOPLE_KEYS = {"UK": ("officers", "persons_with_significant_control"), "PT": ("orgaos_sociais", "socios"), "BR": ("qsa",)}
_BAD_COUNTRY = "country must be one of UK, PT, BR."
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
_NEWS_LANG = {"UK": "en", "PT": "pt", "BR":"pt"}   # ISO 639-1 codes


def _reg_path(country: str, reg: str) -> str:
    """Registry path for a number. PT/BR numbers are reduced to digits: a CNPJ's '/' would otherwise split the URL path."""
    reg = reg.strip()
    reg = "".join(c for c in reg if c.isdigit()) if country in ("PT", "BR") else reg.upper()
    return _LOOKUP[country].format(reg)


def _ok(data: Any) -> dict:
    return data


def _err(msg: str) -> dict:
    return {"error": msg}


async def _fetch(path: str, **params: Any) -> tuple[Any | None, dict | None]:
    """One call through the retrying client. Returns (data, None), (None, None) for 404, or (None, error_result)."""
    try:
        async with MockDataClient(MOCKDATA_URL) as api:
            return await api.get(path, **params), None
    except httpx.HTTPStatusError as e:
        if e.response.status_code < 500:  # e.g. 400 for a malformed CNPJ: the API message is already actionable
            try:
                detail = e.response.json().get("detail", "")
            except ValueError:
                detail = ""
            return None, _err(f"The request was rejected: {detail or e.response.status_code}")
    except httpx.TransportError:
        pass
    return None, _err(
        "This data source is temporarily unavailable (retried several times). "
        "Do not assume the company has no record there; say in your report that this source could not be checked."
    )

class FinancialFields(BaseModel):
    """The fields every financial summary must populate, each with the search_documents query that finds it."""
    year: str = "Give me the financial year of the most recent annual report for the company"
    revenue: str = "Give me revenue (turnover) for the company"
    net_profit: str = "Give me net profit or loss for the company"
    total_assets: str = "Give me total assets for the company"
    going_concern: str = ("Does the report state a material uncertainty about the company continuing as a "
                          "going concern?")


async def _financial_fields(company_name: str, country: str) -> dict:
    """Run one search_documents call per field; each field gets its best chunks as evidence."""
    queries = FinancialFields().model_dump()
    results = await asyncio.gather(*(search_documents(q, company_name, country, limit=1) for q in queries.values()))
    return {name: {"query": q, **r, "source": (r.get("results") or [{}])[0].get("citation_id")}
            for (name, q), r in zip(queries.items(), results)}


mcp = FastMCP(
    "kyc-data",
    instructions="Tools for KYC research on companies in the UK, Portugal and Brazil. All data is fictional.",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8100")),
    # Local development inside docker compose: accept the service hostname as well as localhost.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

@mcp.tool(
    name="summary_get_financial_statements",
    description="Get the annual-report extract for a company, by registration number (UK number, PT NIPC or BR CNPJ). Returns "
    "the most recent year's report text in `reports` and the metadata of every year on file in `available`. "
    "Read the report's own unit line before copying numbers: tables are often 'in thousands' (milhares / R$ mil), "
    "so multiply to full currency units for revenue, net_profit and total_assets, and say so in the summary. "
    "Use the revenue line (turnover / volume de negocios / receita operacional liquida) and keep the sign of a loss. "
    "If `consolidated` is true the figures are for the whole group, not the company alone: say that in the summary. "
    "going_concern_uncertainty is true only if the report states a material uncertainty about continuing; "
    "the standard 'prepared on a going concern basis' wording is false. An empty `reports` list means no accounts "
    "were found: leave financial_summary null and raise a missing_data flag. Cite the doc_id as the source. "
    "Also needs company_name (as in the register). `fields` holds, for each of year, revenue, net_profit, "
    "total_assets and going_concern, the query used and the best matching report chunks (search_documents "
    "results, each with a citation_id) and a `source` (the citation_id of the chunk backing that field, null if none): "
    "fill each field from its own chunks, check company, country and year, and cite each field's `source` "
    "in financial_summary.sources.",
)
async def get_financial_statements(registration_number: str, country: str, company_name: str) -> dict:
    country = country.upper()
    if country not in _LOOKUP:
        return _err(_BAD_COUNTRY)
    data, err = await _fetch("/documents", type="annual_report", country=country,
                             registration_number=registration_number)
    if err:
        return err
    docs = sorted(data["items"], key=lambda d: d["year"], reverse=True)
    if not docs:
        return _ok({"available": [], "reports": [], "fields": {}})
    fields = await _financial_fields(company_name, country)
    reports = []
    for d in (d for d in docs if d["year"] == docs[0]["year"]):
        full, err = await _fetch(f"/documents/{d['doc_id']}")
        if err:
            return err
        reports.append({"doc_id": d["doc_id"], "year": d["year"], "language": d["language"],
                        "consolidated": d["consolidated"], "content": full["content"],
                        "source": f"mockdata/documents/{d['doc_id']}"})
    return _ok({"available": [{k: d[k] for k in ("doc_id", "year", "consolidated")} for d in docs],
                "reports": reports, "fields": fields})

@mcp.tool()
async def uk_search_companies(name: str, limit: int = 5) -> dict:
    """Search the UK company register by company name.

    Use this first to find a UK company's 8-character company number, then look up
    its profile, officers and owners with the number.

    Args:
        name: Full or partial company name, e.g. "Hawksmoor Meridian Textiles".
        limit: Maximum number of matches to return (1 to 20).

    Returns matches with company_number, title (registered name), company_status,
    date_of_creation and address_snippet. An empty list means no plausible match.
    """
    async with httpx.AsyncClient(base_url=MOCKDATA_URL, timeout=15) as client:
        r = await client.get("/uk/search", params={"q": name, "limit": max(1, min(limit, 20))})
        if r.status_code >= 500:
            return {"error": "The UK register is temporarily unavailable. Try again in a few seconds."}
        r.raise_for_status()
        data = r.json()
    return {"matches": data["items"], "source": f"{MOCKDATA_URL}/uk/search?q={name}"}


@mcp.tool(
    name="registry_search_company",
    description="Find a company's registration number from its name. Call this first when you only have a name. "
    "UK and PT only: searches the official register and returns candidates (registration_number, legal_name, status). "
    "Similar names are common, so compare name, status and address with what you were given before choosing one; "
    "if two stay plausible, report the ambiguity instead of guessing. "
    "For BR use br_find_prospect_cnpj instead: the CNPJ register cannot be searched by name. "
    "An empty list means no plausible match; it does not prove the company does not exist.",
)
async def registry_search_company(name: str, country: str) -> dict:
    country = country.upper()
    if country == "UK":
        data, err = await _fetch("/uk/search", q=name, limit=5)
        rows = data and [{"registration_number": i["company_number"], "legal_name": i["title"],
                          "status": i["company_status"], "incorporated": i["date_of_creation"],
                          "address": i["address_snippet"]} for i in data["items"]]
    elif country == "PT":
        data, err = await _fetch("/pt/pesquisa", nome=name, limite=5)
        rows = data and [{"registration_number": i["nipc"], "legal_name": i["denominacao"],
                          "status": i["estado"], "locality": i["localidade"]} for i in data["resultados"]]
    elif country == "BR":
        return _err("BR companies cannot be searched by name in the register. Use br_find_prospect_cnpj.")
    else:
        return _err(_BAD_COUNTRY)
    return err or _ok({"country": country, "matches": rows or []})


@mcp.tool(
    name="br_find_prospect_cnpj",
    description="BR only: find the CNPJ a prospect declared, from the company name. The Brazilian CNPJ register cannot be "
    "searched by name, so this searches the bank's onboarding requests instead. "
    "The returned data is declared by the prospect and unverified, and may differ from the register: always confirm "
    "the CNPJ with registry_get_company (country BR) and take identity, status and address from that record. "
    "Similar names are possible: if two candidates stay plausible, report the ambiguity instead of guessing. "
    "An empty list means no onboarding request matches; it does not prove the company does not exist.",
)
async def br_find_prospect_cnpj(name: str) -> dict:
    data, err = await _fetch("/bank/prospects", name=name, country="BR", limit=5)
    return err or _ok({"country": "BR", "source": "bank onboarding requests (declared, unverified)",
                       "matches": (data and data["items"]) or []})


@mcp.tool(
    name="registry_get_company",
    description="Get the official register record for one company by registration number: legal name, status, incorporation "
    "date, address, activity/sector code and legal form. Use it after registry_search_company (UK/PT) or br_find_prospect_cnpj (BR), or directly if you already "
    "have the number. Formats: UK 8 characters (01234567 or SC123456); PT NIPC 9 digits; BR CNPJ 14 digits, "
    "punctuation optional. This is the authoritative record: when it conflicts with prospect-declared data or "
    "news, prefer it and flag the conflict. It does not include people; use registry_get_officers_and_owners for that.",
)
async def registry_get_company(country: str, registration_number: str) -> dict:
    country, reg = country.upper(), registration_number
    if country not in _LOOKUP:
        return _err(_BAD_COUNTRY)
    path = _reg_path(country, reg)
    data, err = await _fetch(path)
    if err:
        return err
    if data is None:
        return _err(f"No {country} company with registration number '{reg}'. "
                    "Check the format, or find the number with registry_search_company (UK/PT) or br_find_prospect_cnpj (BR).")
    record = {k: v for k, v in data.items() if k not in _PEOPLE_KEYS[country]}
    return _ok({"record": record, "source": f"mockdata{path}"})


@mcp.tool(
    name="registry_get_officers_and_owners",
    description="List who runs and who owns a company: directors/officers and owners (UK: persons with significant control; "
    "PT: orgaos_sociais and socios; BR: the QSA partner list). Needs the registration number. An owner can itself be "
    "a company (holding chain): call this tool again with that owner's registration number to reach the real "
    "people. Two different people can share a name; match on date of birth or nationality before treating "
    "them as the same person. Report owners from offshore jurisdictions as found; do not judge them here.",
)
async def registry_get_officers_and_owners(country: str, registration_number: str) -> dict:
    country, reg = country.upper(), registration_number
    if country not in _LOOKUP:
        return _err(_BAD_COUNTRY)
    if country == "UK":
        base = _reg_path("UK", reg)
        officers, e1 = await _fetch(f"{base}/officers")
        owners, e2 = await _fetch(f"{base}/persons-with-significant-control")
        if e1 or e2:
            return e1 or e2
        if officers is None or owners is None:
            return _err(f"No UK company with number '{reg}'. Find it with registry_search_company first.")
        return _ok({"officers": officers["items"], "owners": owners["items"], "source": f"mockdata/uk/company/{reg}"})
    path = _reg_path(country, reg)
    data, err = await _fetch(path)
    if err:
        return err
    if data is None:
        return _err(f"No {country} company with registration number '{reg}'. Find it with "
                    f"{'br_find_prospect_cnpj' if country == 'BR' else 'registry_search_company'} first.")
    return _ok({**{k: data.get(k, []) for k in _PEOPLE_KEYS[country]}, "source": f"mockdata{path}"})


async def _owners_lookup(country: str, reg: str) -> tuple[list[RawOwner] | None, str]:
    """One register hop for the chain walker: (owners, source) or (None, source) for a 404; LookupError if unavailable."""
    if country == "UK":
        path = f"{_reg_path('UK', reg)}/persons-with-significant-control"
        data, err = await _fetch(path)
        records = (data or {}).get("items")
        ref = f"mockdata/uk/company/{reg}"
    else:
        path = _reg_path(country, reg)
        data, err = await _fetch(path)
        records = (data or {}).get(_PEOPLE_KEYS[country][-1])
        ref = f"mockdata{path}"
    if err:
        raise LookupError(err["error"])
    return (normalize_owners(country, records or []) if data is not None else None), ref


@mcp.tool(
    name="registry_trace_ultimate_owners",
    description="Walk a company's ownership through holding companies to the natural persons behind it, for you. Needs the "
    "country and registration number. Does the look-through in code: calls the register for each corporate owner, "
    "multiplies percentages along the chain, stops at max_depth (default 3, max 10), cuts loops, and applies the "
    "policy's 25% UBO threshold. Each owner comes with `status` (ultimate, below_threshold, intermediate, or an "
    "unresolved_*/depth_limit/cycle dead end), the effective `ownership_pct`, `via` (intermediate companies, nearest "
    "first) and `sources` (the register lookups behind it: register each in pack.sources, using `reference` as given "
    "and citing them on the owner). Dead ends are reported, never guessed: `untraced_pct` and `ubo_unidentified` tell "
    "you whether to raise the ubo_unidentified flag; `has_corporate_owner` is the complex_ownership condition. "
    "Jurisdictions are reported, not judged. Use it instead of repeated registry_get_officers_and_owners calls for owners.",
)
async def registry_trace_ultimate_owners(country: str, registration_number: str, max_depth: int = 3) -> dict:
    country = country.upper()
    if country not in _LOOKUP:
        return _err(_BAD_COUNTRY)
    result = await trace_ownership(country, registration_number.strip(), _owners_lookup, max_depth)
    if "error" in result:
        return _err(result["error"] + " Find the number with registry_search_company first; "
                    "if a source was unavailable, say it could not be checked.")
    return _ok(result)


_LEGAL_SUFFIX = re.compile(r"[\s,]+(?:ltda|s\.?\s?a|lda|ltd|limited|plc)\.?$", re.I)


def _short_name(name: str) -> str:
    """Drop the legal-form suffix (Ltd, Limited, PLC, Lda, Ltda, S.A.): articles use the short name, and the archive
    only matches articles that contain every query word."""
    return _LEGAL_SUFFIX.sub("", name.strip()).strip(" ,") or name.strip()


def _news_article(item: NewsItem, source_type: str, title: str, reference: str) -> dict:
    """Validate an article against the pack's NewsItem schema (extra fields are rejected) and pair it with the
    pack `Source` fields (type 'news' = offline archive, 'web' = live) so the origin travels in the schema."""
    return {"news_item": item.model_dump(),
            "source": {"type": source_type, "title": title, "reference": reference}}


@mcp.tool(
    name="news_search",
    description="Search news articles that mention a company in the bank's archive. Returns a list of the pack's NewsItem "
    "(title, url, published_at, language, summary), up to 10. Each item's `sources` already holds the article id: "
    "register that id in pack.sources (type 'news', reference = the id, title = the article title) and keep it in "
    "`sources`. Matching is by words, so an article can be about a DIFFERENT company with a similar name: check the "
    "sector, city or people in the article against the register record before treating it as adverse media about "
    "this company. Pass country to narrow results (PT and BR articles are in Portuguese). Legal suffixes (Ltd, Lda, S.A.) "
    "are dropped for you, and every word of the name must appear in an article, so use the trading name without "
    "extra words. An empty list means no "
    "coverage was found, not that the company is clean. Copy the items into pack.news unchanged, then set sentiment "
    "and severity yourself (the tool does not judge). Add NO other fields: the schema rejects them.",
)
async def news_search(company_name: str, country: str | None = None) -> list[NewsItem]:
    data, err = await _fetch("/news/search", q=_short_name(company_name), country=(country or "").upper() or None,
                             limit=10)
    if err:
        raise RuntimeError(err["error"])
    return [NewsItem(title=a["title"], url=a["url"], published_at=a["published_at"], language=a["language"],
                     summary=a["body"][:400], sources=[a["id"]]) for a in data["items"]]


@mcp.tool(
    name="bank_get_relationship",
    description="Check whether the bank already has a relationship with this company, by registration number (UK number, PT "
    "NIPC or BR CNPJ). Returns existing_customer true/false and, if true, customer_since, products, "
    "internal_risk_rating and last_kyc_review. existing_customer false is a normal answer, not an error. "
    "Bank-held data can disagree with the register; if it does, report both versions and do not pick one.",
)
async def bank_get_relationship(registration_number: str) -> dict:
    data, err = await _fetch(f"/bank/relationships/{registration_number}")
    return err or _ok(data)

@mcp.tool(
    name="search_documents",
    description="Search the annual reports and KYC policy documents by company name and content (not by registration "
    "number: many reports have none in the index). Put the company name in the query together with what you need, "
    "e.g. 'Hawksmoor Meridian Textiles turnover 2024' or 'ownership threshold beneficial owner'. Returns the best "
    "chunks, each with `text`, a `citation_id` (cite it as the source), and company, country, year and section. "
    "Names can collide: check company, country and year against the register record before using a chunk. "
    "Read the report's unit line before copying numbers (see summary_get_financial_statements). "
    "An empty list means nothing matched; it does not prove the company has no report.",
)
async def search_documents(query: str, company_name:str, country:str, limit: int = 5) -> dict:
    try:
        hits = await asyncio.to_thread(search, f"{country}/{company_name}: {query}", max(1, min(limit, 10)))
    except (psycopg.Error, httpx.HTTPError):
        return _err("The document index is unavailable (database or embedding service down, or not yet ingested). "
                    "Say in your report that documents could not be searched.")
    return _ok({"results": [
        {"citation_id": f"{h['doc_id']}#{h['chunk_idx']}", "text": h["text"],
         **{k: h["metadata"].get(k) for k in ("company", "country", "year", "section")},
         "score": round(h["score"], 4)} for h in hits]})


@mcp.tool(
    name="bank_get_risk_appetite",
    description="Get the bank's risk appetite: prohibited sectors, high-risk sectors, high-risk jurisdictions and the "
    "ownership threshold for beneficial owners. Takes no input and is the same for every company, so call it "
    "once per run. Compare the company's sector and the countries of its owners against it, and raise matches as "
    "flags in neutral wording. Do not decide on the recommendation from this alone.",
)
async def bank_get_risk_appetite() -> dict:
    data, err = await _fetch("/bank/risk-appetite")
    return err or _ok(data)


_POLICY_DOC = "kyc_policy.md"


@mcp.tool(
    name="policy_search",
    description="Look up the exact text of the bank's KYC policy by describing what you need, e.g. 'Get me the risk "
    "rating points system' or 'recommendation rules' or 'ownership threshold for UBOs'. Returns the best matching "
    "section as `section_id`, `heading`, the full section `text` and a `source` (e.g. 'kyc_policy.md#8'), plus the "
    "headings of other possibly relevant sections in `also_relevant` (search again to read one). The policy is only "
    "available through this tool. Apply the rules exactly as written. To cite it, add a pack source of type "
    "'document' with `reference` = `source` and `excerpt` = the rule you applied copied verbatim from `text`, and "
    "list that source id on each risk factor that applies the rule.",
)
async def policy_search(query: str) -> dict:
    try:
        hits = await asyncio.to_thread(search, f"KYC policy: {query}", 6, doc_id="kyc_policy")
    except (psycopg.Error, httpx.HTTPError):
        return _err("The document index is unavailable (database or embedding service down, or not yet ingested). "
                    "Say in your report that the policy could not be searched.")
    sections = []
    for h in hits:  # numbered policy sections only; the title preamble has no number to cite
        m = re.match(r"(\d+)\.\s*(.*)", h["metadata"].get("section") or "")
        if m:
            sections.append({"section_id": m.group(1), "heading": m.group(2), "text": h["text"]})
    if not sections:
        return _err("No policy section matches. Rephrase using policy words, e.g. 'risk rating points', "
                    "'recommendation', 'ownership and control', 'adverse media', 'flag types'.")
    top = sections[0]
    return _ok({**top, "source": f"{_POLICY_DOC}#{top['section_id']}",
                "also_relevant": [f"{s['section_id']}. {s['heading']}" for s in sections[1:3]]})


# TODO (Day 3): add document search and more resources.
@mcp.resource(
    "policy://kyc",
    name="kyc_policy",
    title="Business Customer KYC Policy",
    description="The bank's KYC policy: identification, ownership, risk-point rules, recommendation thresholds.",
    mime_type="text/markdown",
)
async def kyc_policy() -> str:
    data, err = await _fetch("/documents/kyc_policy")
    if err:
        raise RuntimeError(err["error"])
    return data["content"]

@mcp.tool(
    name="news_search_web",
    description="Search LIVE web news about a company, in the local language of its country "
    "(English for UK, Portuguese for PT and BR). Each article is a `news_item` (the pack's NewsItem) plus the `source` fields (type 'web', title, reference = url) "
    "to register it in pack.sources. Use it in addition to news_search. Matching is by words, so an article can be about "
    "a DIFFERENT company with a similar name: check sector, city or people against the register record. "
    "No results means no coverage was found, not that the company is clean. Source type 'web' marks it as live, "
    "outside the bank's archive: say so when you report it. Copy `news_item` into pack.news unchanged, then set "
    "sentiment, severity and sources yourself. Add NO other fields. Cite the url as the source.",
)
async def news_search_web(company_name: str, country: str) -> dict:
    country = country.upper()
    if country not in _NEWS_LANG:
        return _err(_BAD_COUNTRY)
    if not TAVILY_API_KEY:
        return _err("Live news search is not configured (TAVILY_API_KEY missing). "
                    "Say in your report that live news could not be checked.")
    body = {"query": f'"{company_name}"', "topic": "news", "max_results": 8, "time_range": "year",
            "language": _NEWS_LANG[country], "filter_by_language": True, "exact_match": True}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post("https://api.tavily.com/search", json=body,
                                  headers={"Authorization": f"Bearer {TAVILY_API_KEY}"})
            r.raise_for_status()
    except httpx.HTTPError:
        return _err("Live news search is temporarily unavailable. "
                    "Say in your report that live news could not be checked.")
    articles = [_news_article(
        NewsItem(title=a["title"], url=a["url"], published_at=a.get("published_date"), language=_NEWS_LANG[country],
                 summary=a["content"][:400]),
        source_type="web", title=f'[Live web] {a["title"]}', reference=a["url"]) for a in r.json()["results"]]
    return _ok({"language": _NEWS_LANG[country], "articles": articles})


@mcp.prompt(
    name="kyc_pack_review",
    title="KYC Pack Review",
    description="Template for a human reviewer (or agent) to review a generated KYC pack and decide approve, decline or send back.",
)
def kyc_pack_review(company_name: str, country: str, pack_json: str = "") -> str:
    pack = f"\n\n<kyc_pack>\n{pack_json}\n</kyc_pack>" if pack_json.strip() else ""
    return f"""You are reviewing the KYC pack for {company_name} ({country}) before it goes to a decision.

Read the bank's KYC policy (resource policy://kyc) first, and call bank_get_risk_appetite once. Then check the pack:

1. Identification: do the company name, registration number, status and address agree with the registry (registry_get_company)?
2. Ownership: are all owners above the policy's ownership threshold listed, with nationality and percentage? Are officers and owners complete against registry_get_officers_and_owners?
3. Risk points: recompute the risk points from the policy's rules and compare with the pack's risk score and level.
4. Flags: is every flag backed by a listed source, in neutral wording, with a sensible severity? Is anything in the risk appetite (sectors, jurisdictions) or in news_search results missing as a flag?
5. Recommendation: does it follow from the policy thresholds and the evidence, not from one signal alone?

Report, in this order:
- Verdict: approved, declined or sent_back
- Findings: each issue with the pack field it concerns and the source that shows it
- Comments: 2-3 sentences for the review record

Do not guess. If evidence is missing, say what is missing and send the pack back.{pack}"""


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
