"""Register vs bank-system comparison: "when sources conflict, show both side by side and flag it, never resolve silently".

`compare_register_to_bank` is pure (no I/O) so it can be tested alone. `build_bank_check_server` wraps it in an in-process
SDK tool for the `bank` subagent: it fetches the register record and the bank's prospect (onboarding request) record for
the same company, and returns ready-made `conflicting_data` flags and the two source entries. The agent copies them verbatim.

Fields in common: name, registration number, address, directors. Values are normalised (case, accents, punctuation, legal-form
suffixes, 'SURNAME, First' order, number formatting, address abbreviations); whatever still differs is a conflict.
"""
from __future__ import annotations

import json
import re
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool
from mcp.types import ToolAnnotations

from kyc_common.normalize import norm_person, norm_reg, norm_text, similarity

from ..context import PipelineContext
from ..mockdata_client import MockDataClient

REG_SOURCE_ID = "bank-reg"
PROSPECT_SOURCE_ID = "bank-prospect"
SERVER_NAME = "bankcheck"
TOOL_NAME = "compare_register_with_bank"
FULL_TOOL_NAME = f"mcp__{SERVER_NAME}__{TOOL_NAME}"

_ABBREVIATIONS = {"st": "street", "rd": "road", "ave": "avenue", "av": "avenida", "r": "rua", "pc": "praca", "tv": "travessa",
                  "dr": "doutor", "sr": "senhor", "n": "", "no": ""}
_SAME_PROSPECT_MIN_SIMILARITY = 0.85
_LEGAL_FORMS = {"limited", "ltd", "plc", "lda", "ltda", "sa", "sgps", "unipessoal", "eireli", "me", "epp"}


def _norm_name(s: str | None) -> str:
    """Company name without case, accents, punctuation or legal-form words (whole words only)."""
    text = re.sub(r"\bs a\b", "sa", norm_text(s))  # 'S.A.' normalises to 's a'
    return " ".join(w for w in text.split() if w not in _LEGAL_FORMS)


def _norm_address_text(s: str | None) -> str:
    return " ".join(_ABBREVIATIONS.get(t, t) for t in norm_text(s).split() if _ABBREVIATIONS.get(t, t))


def _norm_postcode(s: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _join(*parts: Any) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


# ---- the common fields of one register record / prospect record ----
def common_from_register(country: str, rec: dict) -> dict:
    """`rec` is the full register record (it includes the people)."""
    country = country.upper()
    if country == "UK":
        a = rec.get("registered_office_address") or {}
        return {"name": rec.get("company_name"), "reg": rec.get("company_number"),
                "line1": _join(a.get("premises"), a.get("address_line_1"), a.get("address_line_2")), "district": None,
                "city": a.get("locality"), "postcode": a.get("postal_code"),
                "directors": [o.get("name") for o in rec.get("officers") or []
                              if o.get("officer_role", "director") == "director"]}  # a company secretary is not a director
    if country == "PT":
        a = rec.get("sede") or {}
        return {"name": rec.get("denominacao"), "reg": rec.get("nipc"), "line1": a.get("morada"), "district": None,
                "city": a.get("localidade"), "postcode": a.get("codigo_postal"),
                "directors": [o.get("nome") for o in rec.get("orgaos_sociais") or []]}
    return {"name": rec.get("razao_social"), "reg": rec.get("cnpj"),
            "line1": _join(rec.get("logradouro"), rec.get("numero")), "district": rec.get("bairro"),
            "city": rec.get("municipio"), "postcode": rec.get("cep"),
            "directors": [o.get("nome_socio") for o in rec.get("qsa") or []
                          if "administrador" in norm_text(o.get("qualificacao_socio"))]}  # persons who run it, not shareholders


def common_from_prospect(p: dict) -> dict:
    a = p.get("declared_address") or {}
    return {"name": p.get("declared_name"), "reg": p.get("declared_registration_number"), "line1": a.get("line1"),
            "district": a.get("district"), "city": a.get("city"), "postcode": a.get("postcode"),
            "directors": list(p.get("declared_directors") or [])}


def _address_text(c: dict) -> str:
    return _join(c["line1"], c["district"], c["city"], c["postcode"])


def _people(c: dict) -> dict[str, str]:
    """normalised name -> name as written"""
    return {norm_person(n): n for n in c["directors"] if norm_person(n)}


def compare_register_to_bank(reg: dict, bank: dict) -> list[dict]:
    """Conflicts between two `common_from_*` dicts: [{field, severity, register, bank}]. Empty when everything matches."""
    out: list[dict] = []

    def add(field: str, severity: str, a: str, b: str) -> None:
        out.append({"field": field, "severity": severity, "register": a, "bank": b})

    if norm_reg(reg["reg"]) != norm_reg(bank["reg"]):
        add("registration_number", "high", str(reg["reg"]), str(bank["reg"]))
    if _norm_name(reg["name"]) != _norm_name(bank["name"]):
        add("name", "medium", str(reg["name"]), str(bank["name"]))

    city_diff = norm_text(reg["city"]) != norm_text(bank["city"])
    post_diff = _norm_postcode(reg["postcode"]) != _norm_postcode(bank["postcode"])
    line_diff = _norm_address_text(reg["line1"]) != _norm_address_text(bank["line1"])
    district_diff = bool(reg["district"] and bank["district"]) and norm_text(reg["district"]) != norm_text(bank["district"])
    if city_diff or post_diff or line_diff or district_diff:
        add("address", "medium" if city_diff or post_diff else "low", _address_text(reg), _address_text(bank))

    r_people, b_people = _people(reg), _people(bank)
    if b_people and r_people.keys() != b_people.keys():
        add("directors", "medium", "; ".join(r_people.values()), "; ".join(b_people.values()))
    return out


def conflict_flags(conflicts: list[dict]) -> list[dict]:
    """One `conflicting_data` flag per conflicting field, both values side by side, both sources cited."""
    flags = []
    for c in conflicts:
        msg = (f"{c['field']}: register \"{c['register']}\" [{REG_SOURCE_ID}] vs bank prospect record \"{c['bank']}\" "
               f"[{PROSPECT_SOURCE_ID}]. The register prevails for identity; a human should confirm which is correct.")
        if c["field"] == "registration_number":
            msg += " The bank record may describe a different entity: check that the company match itself is right."
        flags.append({"type": "conflicting_data", "severity": c["severity"], "message": msg,
                      "sources": [REG_SOURCE_ID, PROSPECT_SOURCE_ID]})
    return flags


def pick_prospect(register: dict, prospects: list[dict]) -> dict | None:
    """The bank prospect record that is about this company: same number first, then shared directors, then the best name match.
    A candidate must at least have a similar name; otherwise the bank has no record of it (not a conflict)."""
    cands = [p for p in prospects if similarity(_norm_name(p.get("declared_name")), _norm_name(register["name"]))
             >= _SAME_PROSPECT_MIN_SIMILARITY]
    if not cands:
        return None
    for p in cands:
        if norm_reg(p.get("declared_registration_number")) == norm_reg(register["reg"]):
            return p
    mine = set(_people(register))
    best = max(cands, key=lambda p: len(mine & set(_people(common_from_prospect(p)))))
    return best


# ---- the SDK tool ----
def _result(payload: dict, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}], **({"is_error": True} if error else {})}


_REGISTER_PATH = {"UK": "/uk/company/{}", "PT": "/pt/entidade/{}", "BR": "/br/cnpj/{}"}


async def fetch_and_compare(client: MockDataClient, country: str, registration_number: str) -> dict:
    country = country.upper()
    if country not in _REGISTER_PATH:
        return {"error": "country must be one of UK, PT, BR."}
    reg_no = registration_number.strip()
    reg_no = "".join(c for c in reg_no if c.isdigit()) if country in ("PT", "BR") else reg_no.upper()
    path = _REGISTER_PATH[country].format(reg_no)
    rec = await client.get(path)
    if rec is None:
        return {"error": f"No {country} company with registration number '{registration_number}' in the register."}
    if country == "UK":  # the profile endpoint leaves the people out
        officers = await client.get(f"{path}/officers")
        rec = {**rec, "officers": (officers or {}).get("items", [])}
    register = common_from_register(country, rec)
    found = await client.get("/bank/prospects", name=register["name"], country=country, limit=10)
    prospect = pick_prospect(register, (found or {}).get("items", []))
    if prospect is None:
        return {"prospect_found": False, "conflicts": [], "flags": [], "sources": [],
                "note": "The bank holds no onboarding (prospect) record for this company: nothing to compare, not a conflict."}
    conflicts = compare_register_to_bank(register, common_from_prospect(prospect))
    sources = [
        {"id": REG_SOURCE_ID, "type": "registry", "title": f"{country} register: company record", "reference": f"mockdata{path}"},
        {"id": PROSPECT_SOURCE_ID, "type": "bank_system", "title": "Bank onboarding request (declared data)",
         "reference": f"mockdata/bank/prospects/{prospect['prospect_id']}"}]
    return {"prospect_found": True, "prospect_id": prospect["prospect_id"], "conflicts": conflicts,
            "flags": conflict_flags(conflicts), "sources": sources}


def build_bank_check_server(ctx: PipelineContext):
    @tool(TOOL_NAME, "Compare the register record with the bank's own record (onboarding request) for one company: name, "
          "registration number, address and directors, after normalising formatting. Needs the country and registration "
          "number. Returns `flags` (ready-made conflicting_data flags, one per differing field, both values side by side) "
          "and the two `sources` they cite. Copy `flags` and `sources` into your reply unchanged; an empty `flags` list "
          "means no conflict was found.",
          {"type": "object", "properties": {"country": {"type": "string"}, "registration_number": {"type": "string"}},
           "required": ["country", "registration_number"]},
          annotations=ToolAnnotations(readOnlyHint=True))
    async def compare(args: dict) -> dict:
        try:
            async with MockDataClient(ctx.settings.mockdata_url) as client:
                data = await fetch_and_compare(client, args["country"], args["registration_number"])
        except Exception as e:  # source unavailable: say so, never pretend there is no conflict
            return _result({"error": f"Register or bank source unavailable, comparison not done: {e}"}, error=True)
        return _result(data, error="error" in data)

    return create_sdk_mcp_server(SERVER_NAME, tools=[compare])
