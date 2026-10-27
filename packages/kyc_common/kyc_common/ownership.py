"""Walk an ownership chain through holding companies to the ultimate beneficial owners (KYC policy section 4).

Deterministic on purpose: percentages are multiplied along the chain, the depth is capped, cycles are cut, and every
owner carries the register lookups that revealed it. The caller supplies `lookup`, so this module never touches the
network and can be tested with plain dicts.

  depth 1 = the direct owners of the root company; a corporate owner is expanded (one more register lookup) only while
  its depth is below `max_depth`, so with max_depth=3 the chain root <- A <- B <- person is walked, but not a fourth hop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

UBO_THRESHOLD_PCT = 25.0
DEFAULT_MAX_DEPTH = 3
MAX_ALLOWED_DEPTH = 10

# jurisdiction text (lowercase) -> the register that covers it; anything else has no register here
_REGISTER_OF = {
    "england": "UK", "wales": "UK", "scotland": "UK", "northern ireland": "UK", "united kingdom": "UK",
    "england and wales": "UK", "uk": "UK", "gb": "UK",
    "portugal": "PT", "brasil": "BR", "brazil": "BR",
}
_BAND = re.compile(r"ownership-of-shares-(\d+)-to-(\d+)-percent")


@dataclass
class RawOwner:
    """One owner as listed in one company's register entry, normalised across the UK, PT and BR shapes."""
    name: str
    type: str                       # "person" | "company"
    pct: float | None               # direct share of the company that lists it
    jurisdiction: str | None = None
    registration_number: str | None = None
    identity: str | None = None     # nationality/date of birth: tells namesakes apart
    pct_note: str | None = None


# A lookup returns (owners, source_reference); owners is None when the register has no such company.
# It raises LookupError when the register could not be reached.
Lookup = Callable[[str, str], Awaitable[tuple[list[RawOwner] | None, str]]]


def normalize_owners(country: str, records: list[dict]) -> list[RawOwner]:
    """Owner entries from a UK `persons_with_significant_control`, PT `socios` or BR `qsa` list."""
    out: list[RawOwner] = []
    for r in records:
        if country == "UK":
            pct, note = r.get("ownership_percentage"), None
            if pct is None:  # PSC band, e.g. 25 to 50%: the lower bound is what the threshold test can rely on
                m = next((_BAND.search(n) for n in r.get("natures_of_control", []) if _BAND.search(n)), None)
                if m:
                    pct, note = float(m.group(1)), f"band {m.group(1)}-{m.group(2)}%, lower bound used"
            ident = r.get("identification") or {}
            dob = r.get("date_of_birth") or {}
            out.append(RawOwner(
                r["name"], "person" if r.get("kind", "").startswith("individual") else "company", pct,
                ident.get("country_registered") or r.get("nationality"), ident.get("registration_number"),
                f"{r.get('nationality')}|{dob.get('year')}-{dob.get('month')}" if dob else r.get("nationality"), note))
        elif country == "PT":
            company = r.get("tipo") == "pessoa coletiva"
            out.append(RawOwner(
                r["nome"], "company" if company else "person", r.get("participacao_pct"), r.get("pais") or
                (None if company else r.get("nacionalidade")), r.get("nipc") or r.get("numero_registo_estrangeiro"),
                r.get("nacionalidade")))
        else:  # BR: administrators with no capital share are officers, not owners
            if not r.get("percentual_capital_social"):
                continue
            doc = r.get("cnpj_cpf_do_socio") or ""
            company = "/" in doc
            out.append(RawOwner(r["nome_socio"], "company" if company else "person", r["percentual_capital_social"],
                                r.get("pais"), doc if company else None, None if company else doc))
    return out


def register_for(owner: RawOwner, parent_country: str) -> str | None:
    """Which register can expand this corporate owner, or None (foreign or unknown: the chain stops here)."""
    if not owner.registration_number:
        return None
    if not owner.jurisdiction:
        return parent_country
    return _REGISTER_OF.get(owner.jurisdiction.strip().lower())


@dataclass
class _Found:
    name: str
    type: str
    effective: float
    jurisdiction: str | None
    identity: str | None
    status: str
    paths: list[list[str]] = field(default_factory=list)       # intermediate companies, nearest first
    sources: list[list[str]] = field(default_factory=list)     # lookups behind each path, root's first
    notes: list[str] = field(default_factory=list)


async def trace_ownership(country: str, registration_number: str, lookup: Lookup,
                          max_depth: int = DEFAULT_MAX_DEPTH, root_name: str | None = None) -> dict:
    """Trace `registration_number`'s owners to natural persons. Returns a JSON-ready dict (see keys below)."""
    max_depth = max(1, min(int(max_depth), MAX_ALLOWED_DEPTH))
    found: dict[tuple, _Found] = {}
    cache: dict[tuple[str, str], tuple[list[RawOwner] | None, str] | None] = {}
    sources: dict[str, str] = {}   # reference -> what it is
    notes: list[str] = []

    async def get(c: str, reg: str):
        key = (c, reg)
        if key not in cache:
            try:
                cache[key] = await lookup(c, reg)
            except LookupError:
                cache[key] = None
            if cache[key]:
                sources[cache[key][1]] = f"{c} register: owners of {reg}"
        return cache[key]

    def record(o: RawOwner, eff: float, status: str, via: list[str], srcs: list[str], note: str | None = None):
        key = (o.type, o.name.casefold(), o.identity if o.type == "person" else o.registration_number)
        f = found.setdefault(key, _Found(o.name, o.type, 0.0, o.jurisdiction, o.identity, status))
        f.effective += eff
        f.paths.append(via)
        f.sources.append(srcs)
        if note and note not in f.notes:
            f.notes.append(note)

    async def walk(c: str, reg: str, eff: float, depth: int, via: list[str], srcs: list[str], seen: tuple):
        res = await get(c, reg)
        if res is None:
            return False
        owners, ref = res
        if owners is None:
            return False
        for o in owners:
            if not o.pct:
                continue
            e = eff * o.pct / 100.0
            note = o.pct_note
            if o.type == "person":
                record(o, e, "ultimate" if e >= UBO_THRESHOLD_PCT else "below_threshold", via, srcs + [ref], note)
                continue
            reg_c = register_for(o, c)
            key = (reg_c, o.registration_number)
            if reg_c is None:
                record(o, e, "unresolved_no_register", via, srcs + [ref],
                       f"{o.jurisdiction or 'unknown jurisdiction'}: no register available to look through it")
            elif key in seen:
                record(o, e, "cycle", via, srcs + [ref], "ownership loops back to a company already in this chain")
            elif depth >= max_depth:
                record(o, e, "depth_limit", via, srcs + [ref], f"not expanded: depth limit {max_depth} reached")
            else:
                if await walk(reg_c, o.registration_number, e, depth + 1, via + [o.name], srcs + [ref], seen + (key,)):
                    record(o, e, "intermediate", via, srcs + [ref], None)
                else:
                    record(o, e, "unresolved_not_found", via, srcs + [ref],
                           "the register has no owners for this company or could not be checked")
        return True

    root_key = (country, registration_number)
    if not await walk(country, registration_number, 100.0, 1, [], [], (root_key,)):
        return {"error": f"No ownership record for {country} {registration_number}, or the register could not be checked."}

    owners_out = []
    for f in sorted(found.values(), key=lambda x: (-x.effective, x.name)):
        best = max(range(len(f.paths)), key=lambda i: len(f.paths[i]))
        owners_out.append({
            "name": f.name, "type": f.type, "ownership_pct": round(f.effective, 4), "jurisdiction": f.jurisdiction,
            "status": f.status,
            "is_ultimate_beneficial_owner": f.type == "person" and f.effective >= UBO_THRESHOLD_PCT,
            "via": f.paths[best], "other_paths": [p for i, p in enumerate(f.paths) if i != best],
            "sources": sorted({s for ss in f.sources for s in ss}), "notes": f.notes,
        })
    untraced = sum(f.effective for f in found.values()
                   if f.type == "company" and f.status.startswith(("unresolved", "depth", "cycle")))
    return {
        "root": {"country": country, "registration_number": registration_number, "name": root_name},
        "max_depth": max_depth,
        "ubo_threshold_pct": UBO_THRESHOLD_PCT,
        "owners": owners_out,
        "untraced_pct": round(untraced, 4),
        "ubo_unidentified": untraced >= UBO_THRESHOLD_PCT,
        "has_corporate_owner": any(f.type == "company" for f in found.values()),
        "jurisdictions": sorted({f.jurisdiction for f in found.values() if f.type == "company" and f.jurisdiction}),
        "sources": [{"reference": r, "title": t} for r, t in sources.items()],
        "notes": notes,
    }
