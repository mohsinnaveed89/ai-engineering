"""KYC data tools for Agent SDK agents. They wrap the mock data API through the retrying MockDataClient.

Use:  options = ClaudeAgentOptions(mcp_servers={"kyc": kyc_server}, allowed_tools=["mcp__kyc__*"])

Tool descriptions are written for the agent: when to call, input formats, how to read empty results,
the trap for that tool, and what to do next.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from app.mockdata_client import MockDataClient
from app.settings import settings

# Registry lookup path per country. The PT/BR record carries the people; the UK splits them out.
_LOOKUP = {"UK": "/uk/company/{}", "PT": "/pt/entidade/{}", "BR": "/br/cnpj/{}"}
_PEOPLE_KEYS = {"UK": ("officers", "persons_with_significant_control"), "PT": ("orgaos_sociais", "socios"), "BR": ("qsa",)}
_BAD_COUNTRY = "country must be one of UK, PT, BR."


def _reg_path(country: str, reg: str) -> str:
    """Registry path for a number. PT/BR numbers are reduced to digits: a CNPJ's '/' would otherwise split the URL path."""
    reg = reg.strip()
    reg = "".join(c for c in reg if c.isdigit()) if country in ("PT", "BR") else reg.upper()
    return _LOOKUP[country].format(reg)


def _ok(data: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


async def _fetch(path: str, **params: Any) -> tuple[Any | None, dict | None]:
    """One call through the retrying client. Returns (data, None), (None, None) for 404, or (None, error_result)."""
    try:
        async with MockDataClient(settings.mockdata_url) as api:
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


@tool(
    "search_company",
    "Find a company's registration number from its name. Call this first when you only have a name. "
    "UK and PT only: searches the official register and returns candidates (registration_number, legal_name, status). "
    "Similar names are common, so compare name, status and address with what you were given before choosing one; "
    "if two stay plausible, report the ambiguity instead of guessing. "
    "For BR use find_prospect_cnpj instead: the CNPJ register cannot be searched by name. "
    "An empty list means no plausible match; it does not prove the company does not exist.",
    {"name": str, "country": str},
)
async def search_company(args: dict) -> dict:
    name, country = args["name"], args["country"].upper()
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
        return _err("BR companies cannot be searched by name in the register. Use find_prospect_cnpj.")
    else:
        return _err(_BAD_COUNTRY)
    return err or _ok({"country": country, "matches": rows or []})


@tool(
    "find_prospect_cnpj",
    "BR only: find the CNPJ a prospect declared, from the company name. The Brazilian CNPJ register cannot be "
    "searched by name, so this searches the bank's onboarding requests instead. "
    "The returned data is declared by the prospect and unverified, and may differ from the register: always confirm "
    "the CNPJ with get_company_by_registration (country BR) and take identity, status and address from that record. "
    "Similar names are possible: if two candidates stay plausible, report the ambiguity instead of guessing. "
    "An empty list means no onboarding request matches; it does not prove the company does not exist.",
    {"name": str},
)
async def find_prospect_cnpj(args: dict) -> dict:
    data, err = await _fetch("/bank/prospects", name=args["name"], country="BR", limit=5)
    return err or _ok({"country": "BR", "source": "bank onboarding requests (declared, unverified)",
                       "matches": (data and data["items"]) or []})


@tool(
    "get_company_by_registration",
    "Get the official register record for one company by registration number: legal name, status, incorporation "
    "date, address, activity/sector code and legal form. Use it after search_company (UK/PT) or find_prospect_cnpj (BR), or directly if you already "
    "have the number. Formats: UK 8 characters (01234567 or SC123456); PT NIPC 9 digits; BR CNPJ 14 digits, "
    "punctuation optional. This is the authoritative record: when it conflicts with prospect-declared data or "
    "news, prefer it and flag the conflict. It does not include people; use get_officers_and_owners for that.",
    {"country": str, "registration_number": str},
)
async def get_company_by_registration(args: dict) -> dict:
    country, reg = args["country"].upper(), args["registration_number"]
    if country not in _LOOKUP:
        return _err(_BAD_COUNTRY)
    path = _reg_path(country, reg)
    data, err = await _fetch(path)
    if err:
        return err
    if data is None:
        return _err(f"No {country} company with registration number '{reg}'. "
                    "Check the format, or find the number with search_company (UK/PT) or find_prospect_cnpj (BR).")
    record = {k: v for k, v in data.items() if k not in _PEOPLE_KEYS[country]}
    return _ok({"record": record, "source": f"mockdata{path}"})


@tool(
    "get_officers_and_owners",
    "List who runs and who owns a company: directors/officers and owners (UK: persons with significant control; "
    "PT: orgaos_sociais and socios; BR: the QSA partner list). Needs the registration number. An owner can itself be "
    "a company (holding chain): call this tool again with that owner's registration number to reach the real "
    "people. Two different people can share a name; match on date of birth or nationality before treating "
    "them as the same person. Report owners from offshore jurisdictions as found; do not judge them here.",
    {"country": str, "registration_number": str},
)
async def get_officers_and_owners(args: dict) -> dict:
    country, reg = args["country"].upper(), args["registration_number"]
    if country not in _LOOKUP:
        return _err(_BAD_COUNTRY)
    if country == "UK":
        base = _reg_path("UK", reg)
        officers, e1 = await _fetch(f"{base}/officers")
        owners, e2 = await _fetch(f"{base}/persons-with-significant-control")
        if e1 or e2:
            return e1 or e2
        if officers is None or owners is None:
            return _err(f"No UK company with number '{reg}'. Find it with search_company first.")
        return _ok({"officers": officers["items"], "owners": owners["items"], "source": f"mockdata/uk/company/{reg}"})
    path = _reg_path(country, reg)
    data, err = await _fetch(path)
    if err:
        return err
    if data is None:
        return _err(f"No {country} company with registration number '{reg}'. Find it with "
                    f"{'find_prospect_cnpj' if country == 'BR' else 'search_company'} first.")
    return _ok({**{k: data.get(k, []) for k in _PEOPLE_KEYS[country]}, "source": f"mockdata{path}"})


@tool(
    "search_news",
    "Search news articles that mention a company. Returns id, title, date, outlet, language, country, url and a "
    "snippet. Matching is by words, so an article can be about a DIFFERENT company with a similar name: check the "
    "sector, city or people in the article against the register record before treating it as adverse media about "
    "this company. Pass country to narrow results (PT and BR articles are in Portuguese). No results means no "
    "coverage was found, not that the company is clean. Cite the article id as the source.",
    {"company_name": str, "country": str},
)
async def search_news(args: dict) -> dict:
    country = (args.get("country") or "").upper() or None
    data, err = await _fetch("/news/search", q=args["company_name"], country=country, limit=10)
    if err:
        return err
    articles = [{"id": a["id"], "title": a["title"], "published_at": a["published_at"], "source": a["source"],
                 "language": a["language"], "country": a["country"], "url": a["url"], "snippet": a["body"][:400]}
                for a in data["items"]]
    return _ok({"total_results": data["total_results"], "articles": articles})


@tool(
    "get_bank_relationship",
    "Check whether the bank already has a relationship with this company, by registration number (UK number, PT "
    "NIPC or BR CNPJ). Returns existing_customer true/false and, if true, customer_since, products, "
    "internal_risk_rating and last_kyc_review. existing_customer false is a normal answer, not an error. "
    "Bank-held data can disagree with the register; if it does, report both versions and do not pick one.",
    {"registration_number": str},
)
async def get_bank_relationship(args: dict) -> dict:
    data, err = await _fetch(f"/bank/relationships/{args['registration_number']}")
    return err or _ok(data)


@tool(
    "get_bank_risk_appetite",
    "Get the bank's risk appetite: prohibited sectors, high-risk sectors, high-risk jurisdictions and the "
    "ownership threshold for beneficial owners. Takes no input and is the same for every company, so call it "
    "once per run. Compare the company's sector and the countries of its owners against it, and raise matches as "
    "flags in neutral wording. Do not decide on the recommendation from this alone.",
    {},
)
async def get_bank_risk_appetite(args: dict) -> dict:
    data, err = await _fetch("/bank/risk-appetite")
    return err or _ok(data)


@tool(
    "get_financial_statements",
    "Get the annual-report extract for a company, by registration number (UK number, PT NIPC or BR CNPJ). Returns "
    "the most recent year's report text in `reports` and the metadata of every year on file in `available`. "
    "Read the report's own unit line before copying numbers: tables are often 'in thousands' (milhares / R$ mil), "
    "so multiply to full currency units for revenue, net_profit and total_assets, and say so in the summary. "
    "Use the revenue line (turnover / volume de negocios / receita operacional liquida) and keep the sign of a loss. "
    "If `consolidated` is true the figures are for the whole group, not the company alone: say that in the summary. "
    "going_concern_uncertainty is true only if the report states a material uncertainty about continuing; "
    "the standard 'prepared on a going concern basis' wording is false. An empty `reports` list means no accounts "
    "were found: leave financial_summary null and raise a missing_data flag. Cite the doc_id as the source.",
    {"registration_number": str, "country": str},
)
async def get_financial_statements(args: dict) -> dict:
    country = args["country"].upper()
    if country not in _LOOKUP:
        return _err(_BAD_COUNTRY)
    data, err = await _fetch("/documents", type="annual_report", country=country,
                             registration_number=args["registration_number"])
    if err:
        return err
    docs = sorted(data["items"], key=lambda d: d["year"], reverse=True)
    if not docs:
        return _ok({"available": [], "reports": []})
    reports = []
    for d in (d for d in docs if d["year"] == docs[0]["year"]):
        full, err = await _fetch(f"/documents/{d['doc_id']}")
        if err:
            return err
        reports.append({"doc_id": d["doc_id"], "year": d["year"], "language": d["language"],
                        "consolidated": d["consolidated"], "content": full["content"],
                        "source": f"mockdata/documents/{d['doc_id']}"})
    return _ok({"available": [{k: d[k] for k in ("doc_id", "year", "consolidated")} for d in docs],
                "reports": reports})


kyc_server = create_sdk_mcp_server(
    name="kyc",
    version="1.0.0",
    tools=[search_company, find_prospect_cnpj, get_company_by_registration, get_officers_and_owners,
           search_news, get_bank_relationship, get_bank_risk_appetite, get_financial_statements],
)
