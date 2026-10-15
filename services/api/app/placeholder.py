"""
The placeholder pipeline: a valid pack from the mock data, with no AI.

It is deliberately naive. It takes the first search result, does not look
through holding companies, does not read the news or the annual reports, and
never decides anything. It exists so the whole system works end to end on
day zero and gives you a baseline score to beat.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from kyc_common import (CompanyDetails, Flag, KycPack, NewsItem, Officer, Owner, PackQuery, Risk, Source,
                        SourcedValue)

from .context import PipelineContext
from .mockdata_client import MockDataClient


class Sources:
    """Collects sources and hands out ids, one per distinct reference."""

    def __init__(self):
        self.items: list[Source] = []

    def add(self, type_: str, title: str, reference: str, excerpt: str | None = None) -> str:
        for s in self.items:
            if s.reference == reference:
                return s.id
        sid = f"src-{len(self.items) + 1}"
        self.items.append(Source(id=sid, type=type_, title=title, reference=reference, excerpt=excerpt,
                                 retrieved_at=datetime.now(timezone.utc)))
        return sid


def fact(value, sid: str | None) -> SourcedValue:
    return SourcedValue(value=value, sources=[sid] if (sid and value not in (None, "")) else [])


async def build_placeholder_pack(ctx: PipelineContext) -> KycPack:
    src = Sources()
    officers: list[Officer] = []
    owners: list[Owner] = []
    flags: list[Flag] = []
    details = CompanyDetails(legal_name=fact(None, None), registration_number=fact(None, None),
                             status=fact(None, None), address=fact(None, None))
    reg_no = None

    async with MockDataClient(ctx.settings.mockdata_url) as api:
        await ctx.emit("registry", f"Looking up the {ctx.country} company register")
        if ctx.country == "UK":
            hits = await api.get("/uk/search", q=ctx.company_name)
            items = (hits or {}).get("items", [])
            if items:
                number = items[0]["company_number"]
                prof = await api.get(f"/uk/company/{number}")
                sid = src.add("registry", "UK register: company profile", api.url(f"/uk/company/{number}"))
                addr = prof.get("registered_office_address")
                details = CompanyDetails(
                    legal_name=fact(prof["company_name"], sid), registration_number=fact(number, sid),
                    status=fact(prof["company_status"], sid),
                    address=fact(", ".join(v for v in addr.values() if v) if addr else None, sid),
                    incorporation_date=fact(prof.get("date_of_creation"), sid),
                    sector=fact(", ".join(prof.get("sic_codes", [])), sid))
                reg_no = number
                offs = await api.get(f"/uk/company/{number}/officers")
                osid = src.add("registry", "UK register: officers", api.url(f"/uk/company/{number}/officers"))
                officers = [Officer(name=o["name"], role=o["officer_role"], appointed_on=o.get("appointed_on"), sources=[osid])
                            for o in (offs or {}).get("items", [])]
                pscs = await api.get(f"/uk/company/{number}/persons-with-significant-control")
                psid = src.add("registry", "UK register: persons with significant control",
                               api.url(f"/uk/company/{number}/persons-with-significant-control"))
                for p in (pscs or {}).get("items", []):
                    owners.append(Owner(name=p["name"], type="person" if p["kind"].startswith("individual") else "company",
                                        sources=[psid]))
        elif ctx.country == "PT":
            hits = await api.get("/pt/pesquisa", nome=ctx.company_name)
            items = (hits or {}).get("resultados", [])
            if items:
                nipc = items[0]["nipc"]
                ent = await api.get(f"/pt/entidade/{nipc}")
                sid = src.add("registry", "Registo comercial (PT): certidão", api.url(f"/pt/entidade/{nipc}"))
                sede = ent.get("sede")
                details = CompanyDetails(
                    legal_name=fact(ent["denominacao"], sid), registration_number=fact(nipc, sid),
                    status=fact(ent["estado"], sid),
                    address=fact(f"{sede['morada']}, {sede['codigo_postal']} {sede['localidade']}" if sede else None, sid),
                    incorporation_date=fact(ent.get("data_constituicao"), sid),
                    sector=fact(f"{ent['cae']['codigo']} {ent['cae']['descricao']}", sid))
                reg_no = nipc
                officers = [Officer(name=o["nome"], role=o["cargo"], appointed_on=o.get("data_designacao"), sources=[sid])
                            for o in ent.get("orgaos_sociais", [])]
                owners = [Owner(name=s["nome"], type="person" if s["tipo"] == "pessoa singular" else "company",
                                ownership_pct=s.get("participacao_pct"), sources=[sid]) for s in ent.get("socios", [])]
        else:  # BR: the register can only be searched by CNPJ, so start from the onboarding request
            prospects = await api.get("/bank/prospects", name=ctx.company_name, country="BR")
            items = (prospects or {}).get("items", [])
            if items:
                p = items[0]
                psid = src.add("bank_system", "Bank onboarding request (declared data)", api.url(f"/bank/prospects/{p['prospect_id']}"))
                cnpj = p["declared_registration_number"]
                digits = re.sub(r"\D", "", cnpj)
                data = await api.get(f"/br/cnpj/{digits}")
                if data:
                    sid = src.add("registry", "Cadastro CNPJ (BR)", api.url(f"/br/cnpj/{digits}"))
                    addr = ", ".join(x for x in [data.get("logradouro"), data.get("numero"), data.get("bairro"),
                                                 data.get("municipio"), data.get("uf"), data.get("cep")] if x) or None
                    details = CompanyDetails(
                        legal_name=fact(data["razao_social"], sid), registration_number=fact(data["cnpj"], sid),
                        status=fact(data["situacao_cadastral"], sid), address=fact(addr, sid),
                        incorporation_date=fact(data.get("data_inicio_atividade"), sid),
                        sector=fact(f"{data['cnae_fiscal']['codigo']} {data['cnae_fiscal']['descricao']}", sid))
                    reg_no = data["cnpj"]
                    for q in data.get("qsa", []):
                        if "Administrador" in q["qualificacao_socio"]:
                            officers.append(Officer(name=q["nome_socio"], role=q["qualificacao_socio"], sources=[sid]))
                        if q.get("percentual_capital_social"):
                            is_company = "/" in (q.get("cnpj_cpf_do_socio") or "") or q["qualificacao_socio"].startswith("37")
                            owners.append(Owner(name=q["nome_socio"], type="company" if is_company else "person",
                                                ownership_pct=q["percentual_capital_social"], sources=[sid]))
                else:
                    flags.append(Flag(type="missing_data", message=f"Declared CNPJ {cnpj} not found in the register.", sources=[psid]))
        await asyncio.sleep(0.3)

        if details.legal_name.value is None:
            flags.append(Flag(type="missing_data", severity="high",
                              message="Company not found in the register by the placeholder (first search result only).",
                              sources=[src.add("other", "Placeholder pipeline", "placeholder://no-match")]))

        await ctx.emit("news", "Collecting news (not analysed)")
        lang = "en" if ctx.country == "UK" else "pt"
        found = await api.get("/news/search", q=ctx.company_name, language=lang, country=ctx.country, limit=10)
        news = []
        for a in (found or {}).get("items", []):
            nsid = src.add("news", f"{a['source']}: {a['title']}", a["url"])
            news.append(NewsItem(title=a["title"], url=a["url"], published_at=a["published_at"], language=a["language"],
                                 summary="Not analysed by the placeholder.", sources=[nsid]))
        await asyncio.sleep(0.3)

        await ctx.emit("documents", "Checking for annual reports (not read)")
        notes = []
        if reg_no:
            docs = await api.get("/documents", type="annual_report", registration_number=reg_no)
            notes = [d["doc_id"] for d in (docs or {}).get("items", [])]
        await asyncio.sleep(0.3)

        await ctx.emit("risk", "Placeholder risk assessment: no analysis performed")
        # Report model usage like this from your agents (the placeholder uses no model).
        await ctx.record_metrics(input_tokens=0, output_tokens=0, cost_usd=0.0, model="none")

    note_parts = []
    if notes:
        note_parts.append(f"Annual reports available but not read: {', '.join(notes)}")
    if ctx.feedback:
        note_parts.append(f"Reviewer feedback received for v{ctx.version} (ignored by the placeholder): {ctx.feedback}")

    return KycPack(
        query=PackQuery(company_name=ctx.company_name, country=ctx.country),
        company=details,
        officers=officers,
        owners=owners,
        news=news,
        financial_summary=None,
        risk=Risk(rating="medium", recommendation="investigate_further",
                  rationale="Placeholder pipeline: no analysis was performed. Replace run_pipeline() in services/api/app/pipeline.py with your agents."),
        flags=flags,
        sources=src.items,
        generated_by="placeholder",
        notes=" | ".join(note_parts) or None,
    )
