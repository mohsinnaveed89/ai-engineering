"""Ownership chain walker: look-through maths, depth limit, cycles, dead ends, sources."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "kyc_common"))

from kyc_common.ownership import RawOwner, normalize_owners, trace_ownership  # noqa: E402


def co(name, pct, reg, jur="England"):
    return RawOwner(name, "company", pct, jur, reg)


def person(name, pct, ident=None):
    return RawOwner(name, "person", pct, "British", None, ident or name)


def run(reg_map, root="R", depth=3):
    async def lookup(country, reg):
        if reg == "DOWN":
            raise LookupError("down")
        return (reg_map[reg], f"src/{reg}") if reg in reg_map else (None, f"src/{reg}")
    return asyncio.run(trace_ownership("UK", root, lookup, depth))


def by_name(res):
    return {o["name"]: o for o in res["owners"]}


def test_multiplies_along_chain_and_cites_every_hop():
    res = run({"R": [co("H1", 80, "H1"), person("Ann", 20)], "H1": [co("H2", 100, "H2"), ], "H2": [person("Bob", 50), person("Cy", 50)]})
    o = by_name(res)
    assert o["Bob"]["ownership_pct"] == 40 and o["Bob"]["is_ultimate_beneficial_owner"]
    assert o["Bob"]["via"] == ["H1", "H2"]
    assert o["Bob"]["sources"] == ["src/H1", "src/H2", "src/R"]
    assert not o["Ann"]["is_ultimate_beneficial_owner"] and o["Ann"]["status"] == "below_threshold"
    assert res["has_corporate_owner"] and not res["ubo_unidentified"]


def test_depth_limit_stops_and_flags():
    chain = {"R": [co("A", 100, "A")], "A": [co("B", 100, "B")], "B": [co("C", 100, "C")], "C": [person("Dee", 100)]}
    res = run(chain, depth=3)
    assert by_name(res)["C"]["status"] == "depth_limit" and "Dee" not in by_name(res)
    assert res["ubo_unidentified"] and res["untraced_pct"] == 100
    assert "Dee" in by_name(run(chain, depth=4))


def test_cycle_is_cut():
    res = run({"R": [co("A", 100, "A")], "A": [co("R-again", 100, "R")]})
    assert by_name(res)["R-again"]["status"] == "cycle" and res["ubo_unidentified"]


def test_foreign_owner_is_a_dead_end_not_a_guess():
    res = run({"R": [co("Bay S.A.", 100, "KY-1", "Cayman Islands")]})
    o = by_name(res)["Bay S.A."]
    assert o["status"] == "unresolved_no_register" and o["jurisdiction"] == "Cayman Islands"
    assert res["ubo_unidentified"] and res["jurisdictions"] == ["Cayman Islands"]


def test_register_unavailable_is_unresolved():
    res = run({"R": [co("X", 60, "DOWN"), person("Eve", 40)]})
    assert by_name(res)["X"]["status"] == "unresolved_not_found" and res["ubo_unidentified"]


def test_small_untraced_share_does_not_flag():
    res = run({"R": [co("Off", 10, "KY-1", "Cayman Islands"), person("Fay", 90)]})
    assert not res["ubo_unidentified"] and res["untraced_pct"] == 10


def test_same_person_via_two_branches_is_summed():
    res = run({"R": [co("A", 50, "A"), co("B", 50, "B")], "A": [person("Gus", 60, "g1")], "B": [person("Gus", 40, "g1")]})
    assert by_name(res)["Gus"]["ownership_pct"] == 50 and by_name(res)["Gus"]["other_paths"]


def test_namesakes_are_not_merged():
    res = run({"R": [person("Hal", 30, "1950"), person("Hal", 30, "1980")]})
    assert len([o for o in res["owners"] if o["name"] == "Hal"]) == 2


def test_missing_root():
    assert "error" in run({}, root="NOPE")


def test_normalizers():
    uk = normalize_owners("UK", [{"kind": "corporate-entity-person-with-significant-control", "name": "H", "natures_of_control": ["ownership-of-shares-25-to-50-percent"], "identification": {"registration_number": "1", "country_registered": "England"}}])
    assert uk[0].pct == 25 and uk[0].type == "company" and uk[0].pct_note
    br = normalize_owners("BR", [{"nome_socio": "ADM", "cnpj_cpf_do_socio": "***1**", "percentual_capital_social": 0, "pais": "BRASIL"},
                                 {"nome_socio": "H LTDA", "cnpj_cpf_do_socio": "21.111.992/0001-02", "percentual_capital_social": 75, "pais": "BRASIL"}])
    assert [o.name for o in br] == ["H LTDA"] and br[0].type == "company"
    pt = normalize_owners("PT", [{"nome": "T Inc", "tipo": "pessoa coletiva", "numero_registo_estrangeiro": "BVI-1", "pais": "British Virgin Islands", "participacao_pct": 100}])
    assert pt[0].type == "company" and pt[0].jurisdiction == "British Virgin Islands"
