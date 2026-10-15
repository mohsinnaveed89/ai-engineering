"""
Mock data API: everything the agents can look up, generated and fully offline.

  /uk/...        UK company register (Companies House style, English)
  /pt/...        Portuguese commercial register (Portuguese field names)
  /br/cnpj/...   Brazilian CNPJ register: lookup by registration number ONLY
  /news/...      News articles in English and Portuguese
  /bank/...      Internal bank systems: onboarding requests, relationships, risk appetite
  /documents/... Annual-report extracts and KYC policies (for RAG)

Open http://localhost:8001/docs for the interactive reference.
All companies and people are fictional.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

DATA = Path(os.getenv("DATA_DIR") or Path(__file__).resolve().parent.parent.parent / "data")
LATENCY_MS = int(os.getenv("MOCK_LATENCY_MS", "60"))
ERROR_RATE = float(os.getenv("MOCK_ERROR_RATE", "0"))

app = FastAPI(title="Mock data API", version="1.0.0",
              description="Fictional registers, news, bank systems and documents for the AI Academy KYC challenge.")


def load(name: str) -> dict:
    return json.loads((DATA / "generated" / name).read_text())


UK = load("uk_register.json")["companies"]
PT = load("pt_register.json")["entidades"]
BR = load("br_register.json")["empresas"]
NEWS = load("news.json")["articles"]
BANK = load("bank.json")
TEST_SET = load("companies.json")["companies"]
DOC_INDEX = json.loads((DATA / "documents" / "index.json").read_text())["documents"]

UK_BY_NO = {c["company_number"]: c for c in UK}
PT_BY_NIPC = {c["nipc"]: c for c in PT}
BR_BY_CNPJ = {re.sub(r"\D", "", c["cnpj"]): c for c in BR}


def norm(s: str | None) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def score(query: str, name: str) -> float:
    q, n = norm(query), norm(name)
    if not q:
        return 0
    tokens = q.split()
    hits = sum(1 for t in tokens if t in n.split())
    return hits / len(tokens) + 0.5 * SequenceMatcher(None, q, n).ratio()


@app.middleware("http")
async def realism(request: Request, call_next):
    """Small latency and optional random failures, so pipelines learn to cope with real-world APIs."""
    if request.url.path not in ("/health", "/docs", "/openapi.json") and not request.url.path.startswith("/meta"):
        if LATENCY_MS:
            await asyncio.sleep(LATENCY_MS / 1000 * random.uniform(0.5, 1.5))
        if ERROR_RATE and random.random() < ERROR_RATE:
            return JSONResponse({"detail": "Upstream register temporarily unavailable. Retry in a few seconds."}, status_code=503)
    return await call_next(request)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "companies": {"UK": len(UK), "PT": len(PT), "BR": len(BR)}, "news": len(NEWS), "documents": len(DOC_INDEX)}


@app.get("/meta/companies", tags=["meta"], summary="The test set: company names and countries an analyst would submit")
def test_companies(country: str | None = None):
    rows = [{"company_id": c["company_id"], "name": c["name"], "country": c["country"]} for c in TEST_SET]
    return {"items": [r for r in rows if not country or r["country"] == country.upper()]}


# ---------------------------------------------------------------- UK register
@app.get("/uk/search", tags=["UK register"], summary="Search UK companies by name")
def uk_search(q: str = Query(..., min_length=2, description="Company name or part of it"), limit: int = Query(5, ge=1, le=20)):
    ranked = sorted(UK, key=lambda c: score(q, c["company_name"]), reverse=True)
    items = [{"company_number": c["company_number"], "title": c["company_name"], "company_status": c["company_status"],
              "date_of_creation": c["date_of_creation"],
              "address_snippet": ", ".join(v for v in (c["registered_office_address"] or {}).values() if v) or None}
             for c in ranked[:limit] if score(q, c["company_name"]) > 0.35]
    return {"total_results": len(items), "items": items}


def uk_get(number: str) -> dict:
    c = UK_BY_NO.get(number.strip().upper())
    if not c:
        raise HTTPException(404, f"No company found with number '{number}'. UK company numbers are 8 characters, e.g. 01234567 or SC123456.")
    return c


@app.get("/uk/company/{company_number}", tags=["UK register"], summary="UK company profile")
def uk_company(company_number: str):
    c = uk_get(company_number)
    return {k: v for k, v in c.items() if k not in ("officers", "persons_with_significant_control")}


@app.get("/uk/company/{company_number}/officers", tags=["UK register"])
def uk_officers(company_number: str):
    c = uk_get(company_number)
    return {"total_results": len(c["officers"]), "items": c["officers"]}


@app.get("/uk/company/{company_number}/persons-with-significant-control", tags=["UK register"])
def uk_pscs(company_number: str):
    c = uk_get(company_number)
    return {"total_results": len(c["persons_with_significant_control"]), "items": c["persons_with_significant_control"]}


# ---------------------------------------------------------------- PT register
@app.get("/pt/pesquisa", tags=["PT register"], summary="Pesquisar entidades por denominação")
def pt_search(nome: str = Query(..., min_length=2, description="Denominação ou parte dela"), limite: int = Query(5, ge=1, le=20)):
    ranked = sorted(PT, key=lambda c: score(nome, c["denominacao"]), reverse=True)
    return {"resultados": [{"nipc": c["nipc"], "denominacao": c["denominacao"], "estado": c["estado"],
                            "localidade": (c["sede"] or {}).get("localidade")}
                           for c in ranked[:limite] if score(nome, c["denominacao"]) > 0.35]}


@app.get("/pt/entidade/{nipc}", tags=["PT register"], summary="Certidão da entidade (por NIPC)")
def pt_entity(nipc: str):
    c = PT_BY_NIPC.get(re.sub(r"\D", "", nipc))
    if not c:
        raise HTTPException(404, f"Entidade não encontrada para o NIPC '{nipc}'. O NIPC tem 9 dígitos.")
    return c


# ---------------------------------------------------------------- BR register
@app.get("/br/cnpj/{cnpj}", tags=["BR register"], summary="Consulta CNPJ (somente por número; não há pesquisa por nome)")
def br_cnpj(cnpj: str):
    digits = re.sub(r"\D", "", cnpj)
    if len(digits) != 14:
        raise HTTPException(400, "CNPJ inválido: informe os 14 dígitos, com ou sem pontuação (ex.: 12.345.678/0001-90).")
    c = BR_BY_CNPJ.get(digits)
    if not c:
        raise HTTPException(404, f"CNPJ {cnpj} não encontrado na base.")
    return c


# ---------------------------------------------------------------- News
@app.get("/news/search", tags=["News"], summary="Search news articles")
def news_search(q: str = Query(..., min_length=2, description="Words that must all appear in the title or body"),
                language: str | None = Query(None, description="en or pt"),
                country: str | None = Query(None, description="UK, PT or BR"),
                limit: int = Query(10, ge=1, le=50)):
    terms = norm(q).split()
    hits = []
    for a in NEWS:
        if language and a["language"] != language.lower():
            continue
        if country and a["country"] != country.upper():
            continue
        text = norm(a["title"] + " " + a["body"])
        if all(t in text.split() for t in terms):
            hits.append(a)
    return {"total_results": len(hits), "items": hits[:limit]}


@app.get("/news/{article_id}", tags=["News"])
def news_article(article_id: str):
    for a in NEWS:
        if a["id"] == article_id:
            return a
    raise HTTPException(404, f"Article '{article_id}' not found.")


# ---------------------------------------------------------------- Bank systems
@app.get("/bank/prospects", tags=["Bank systems"], summary="Onboarding requests submitted by prospects (declared, unverified data)")
def bank_prospects(name: str = Query(..., min_length=2), country: str | None = None, limit: int = Query(5, ge=1, le=20)):
    rows = [p for p in BANK["prospects"] if not country or p["country"] == country.upper()]
    ranked = sorted(rows, key=lambda p: score(name, p["declared_name"]), reverse=True)
    return {"items": [p for p in ranked[:limit] if score(name, p["declared_name"]) > 0.35]}


@app.get("/bank/prospects/{prospect_id}", tags=["Bank systems"])
def bank_prospect(prospect_id: str):
    for p in BANK["prospects"]:
        if p["prospect_id"] == prospect_id:
            return p
    raise HTTPException(404, f"Prospect '{prospect_id}' not found.")


@app.get("/bank/relationships/{registration_number:path}", tags=["Bank systems"], summary="Existing relationship with the bank, if any")
def bank_relationship(registration_number: str):
    key = re.sub(r"[^A-Za-z0-9]", "", registration_number).upper()
    for r in BANK["relationships"]:
        if re.sub(r"[^A-Za-z0-9]", "", r["registration_number"]).upper() == key:
            return {"existing_customer": True, **r}
    return {"existing_customer": False, "registration_number": registration_number}


@app.get("/bank/risk-appetite", tags=["Bank systems"], summary="The bank's risk appetite: prohibited and high-risk sectors and jurisdictions")
def bank_risk_appetite():
    return BANK["risk_appetite"]


# ---------------------------------------------------------------- Documents
@app.get("/documents", tags=["Documents"], summary="List documents (annual-report extracts, KYC policies)")
def documents(type: str | None = Query(None, description="annual_report or policy"), country: str | None = None,
              registration_number: str | None = None, company: str | None = Query(None, description="Company name contains")):
    out = []
    for d in DOC_INDEX:
        if type and d["type"] != type:
            continue
        if country and d["country"] != country.upper():
            continue
        if registration_number and re.sub(r"\W", "", d["registration_number"] or "") != re.sub(r"\W", "", registration_number):
            continue
        if company and norm(company) not in norm(d["company_name"] or ""):
            continue
        out.append(d)
    return {"total_results": len(out), "items": out}


@app.get("/documents/{doc_id}", tags=["Documents"])
def document(doc_id: str):
    for d in DOC_INDEX:
        if d["doc_id"] == doc_id:
            return {**d, "content": (DATA / "documents" / d["path"]).read_text()}
    raise HTTPException(404, f"Document '{doc_id}' not found.")
