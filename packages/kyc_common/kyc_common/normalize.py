"""Normalisation helpers shared by the scoring tool (and handy for pipelines)."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

ACTIVE_WORDS = {"active", "ativa", "ativo"}


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm_text(s: str | None) -> str:
    if not s:
        return ""
    s = strip_accents(str(s)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_person(name: str | None) -> str:
    """'SMITH, John' and 'John Smith' both become 'john smith'."""
    if not name:
        return ""
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first} {last}"
    return norm_text(name)


def norm_company(name: str | None) -> str:
    n = norm_text(name)
    for suffix in (" limited", " ltd", " plc", " lda", " s a", " sa", " ltda", " sgps"):
        n = n.replace(suffix, "")
    return re.sub(r"\s+", " ", n).strip()


def norm_reg(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def norm_status(value: str | None) -> str:
    return "active" if norm_text(value) in ACTIVE_WORDS else ("unknown" if not value else "inactive")


def similarity(a: str | None, b: str | None) -> float:
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jacc = len(ta & tb) / len(ta | tb)
    return max(jacc, SequenceMatcher(None, a, b).ratio())
