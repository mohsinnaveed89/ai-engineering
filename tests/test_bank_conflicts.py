import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api"))

from app.agents.bank_conflicts import (common_from_prospect, common_from_register, compare_register_to_bank,  # noqa: E402
                                       conflict_flags, pick_prospect)

UK_REG = {"company_number": "03176519", "company_name": "BLACKWATER HARBOUR MEDIA PLC",
          "registered_office_address": {"address_line_1": "167 Bridge Street", "locality": "Bristol", "postal_code": "BS1 8BN"},
          "officers": [{"name": "SMITH, Emily"}, {"name": "HARRISON, Fiona"}, {"name": "WOOD, Olivia"}]}
PROSPECT = {"prospect_id": "PRS-1", "declared_name": "Blackwater Harbour Media", "declared_registration_number": "03176519",
            "declared_address": {"line1": "28 Station Road", "city": "Belfast", "postcode": "BT1 9ZA"},
            "declared_directors": ["Emily Smith", "Fiona Harrison", "Olivia Wood"]}


def _fields(reg, prospect):
    return {c["field"]: c for c in compare_register_to_bank(common_from_register("UK", reg), common_from_prospect(prospect))}


def test_uk_020_only_address_conflicts():
    got = _fields(UK_REG, PROSPECT)
    assert list(got) == ["address"]  # PLC suffix, case and 'SURNAME, First' are formatting, not conflicts
    assert got["address"]["severity"] == "medium"
    (flag,) = conflict_flags(list(got.values()))
    assert flag["type"] == "conflicting_data" and "Bristol" in flag["message"] and "Belfast" in flag["message"]
    assert flag["sources"] == ["bank-reg", "bank-prospect"]


def test_registration_number_mismatch_is_high():
    got = _fields(UK_REG, {**PROSPECT, "declared_registration_number": "03176520"})
    assert got["registration_number"]["severity"] == "high"


def test_no_conflict():
    ok = {**PROSPECT, "declared_address": {"line1": "167 bridge st.", "city": "BRISTOL", "postcode": "bs18bn"}}
    assert _fields(UK_REG, ok) == {}


def test_low_severity_when_only_street_differs():
    got = _fields(UK_REG, {**PROSPECT, "declared_address": {"line1": "1 Other Street", "city": "Bristol", "postcode": "BS1 8BN"}})
    assert got["address"]["severity"] == "low"


def test_directors_one_sided():
    got = _fields(UK_REG, {**PROSPECT, "declared_directors": ["Emily Smith", "Someone Else"]})
    assert got["directors"]["severity"] == "medium" and "Someone Else" in got["directors"]["bank"]


def test_pick_prospect():
    reg = common_from_register("UK", UK_REG)
    other = {**PROSPECT, "prospect_id": "PRS-2", "declared_registration_number": "99999999"}
    assert pick_prospect(reg, [other, PROSPECT])["prospect_id"] == "PRS-1"
    assert pick_prospect(reg, [{**PROSPECT, "declared_name": "Totally Different Ltd"}]) is None
