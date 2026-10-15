"""
The KYC onboarding pack: the fixed output schema every pipeline must return.

Rules the schema helps enforce:
  * No source, no claim: every fact carries `sources`, a list of ids that must
    exist in `KycPack.sources`. `sourcing_problems()` lists every violation.
  * Flag, don't judge: concerns go in `flags` for the human to check.
  * The AI recommends, the human decides: `risk.recommendation` is advice;
    `review` records the human decision.

Do not change this schema: the web app and the scoring tool depend on it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Country = Literal["UK", "PT", "BR"]
SourceType = Literal["registry", "news", "bank_system", "document", "web", "other"]
FlagType = Literal[
    "conflicting_data", "missing_data", "complex_ownership", "high_risk_jurisdiction", "ubo_unidentified",
    "adverse_media", "high_risk_sector", "prohibited_sector", "inactive_company", "other",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Model):
    """Where a fact came from. Reference it from facts by `id`."""
    id: str = Field(description="Short unique id, e.g. 'src-1' or 'uk-register-12345678'")
    type: SourceType
    title: str = Field(description="Human-readable label, e.g. 'UK register: company profile'")
    reference: str = Field(description="URL, API path or document id the reviewer can open")
    excerpt: str | None = Field(default=None, description="Optional quote supporting the fact(s)")
    retrieved_at: datetime | None = None


class SourcedValue(_Model):
    """A single fact. A non-null value must cite at least one source."""
    value: str | float | int | None = None
    sources: list[str] = Field(default_factory=list)


class CompanyDetails(_Model):
    legal_name: SourcedValue
    registration_number: SourcedValue
    status: SourcedValue = Field(description="Register status as reported, e.g. 'active', 'ativa', 'ATIVA', 'dissolved'")
    address: SourcedValue = Field(description="Registered address as one string")
    incorporation_date: SourcedValue = Field(default_factory=SourcedValue)
    sector: SourcedValue = Field(default_factory=SourcedValue, description="Sector code and description")


class Officer(_Model):
    name: str
    role: str = Field(description="e.g. director, secretary, Gerente, Administrador, Sócio-Administrador")
    appointed_on: str | None = None
    sources: list[str] = Field(default_factory=list)


class Owner(_Model):
    name: str
    type: Literal["person", "company"]
    ownership_pct: float | None = Field(default=None, description="Effective (look-through) percentage if known")
    is_ultimate_beneficial_owner: bool = False
    jurisdiction: str | None = None
    via: list[str] = Field(default_factory=list, description="Chain of intermediate companies, nearest first")
    sources: list[str] = Field(default_factory=list)


class NewsItem(_Model):
    title: str
    url: str
    published_at: str | None = None
    language: str | None = None
    summary: str = ""
    sentiment: Literal["positive", "neutral", "adverse"] = "neutral"
    severity: Literal["none", "medium", "high"] = "none"
    sources: list[str] = Field(default_factory=list)


class FinancialSummary(_Model):
    year: int | None = None
    currency: str | None = None
    revenue: float | None = None
    net_profit: float | None = None
    total_assets: float | None = None
    going_concern_uncertainty: bool | None = None
    summary: str = ""
    sources: list[str] = Field(default_factory=list)


class RiskFactor(_Model):
    factor: str
    points: int | None = None
    detail: str = ""
    sources: list[str] = Field(default_factory=list)


class Risk(_Model):
    rating: Literal["low", "medium", "high"]
    recommendation: Literal["approve", "decline", "investigate_further"]
    rationale: str
    factors: list[RiskFactor] = Field(default_factory=list)


class Flag(_Model):
    type: FlagType
    severity: Literal["low", "medium", "high"] = "medium"
    message: str
    sources: list[str] = Field(default_factory=list)


class Review(_Model):
    status: Literal["pending", "approved", "declined", "sent_back"] = "pending"
    comments: str | None = None
    reviewer: str | None = None
    reviewed_at: datetime | None = None


class PackQuery(_Model):
    company_name: str
    country: Country


class KycPack(_Model):
    schema_version: Literal["1.0"] = "1.0"
    query: PackQuery
    company: CompanyDetails
    officers: list[Officer] = Field(default_factory=list)
    owners: list[Owner] = Field(default_factory=list)
    news: list[NewsItem] = Field(default_factory=list)
    financial_summary: FinancialSummary | None = None
    risk: Risk
    flags: list[Flag] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    review: Review = Field(default_factory=Review)
    generated_by: str = Field(default="unknown", description="Which pipeline produced the pack, e.g. 'placeholder' or 'agents-v3'")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    notes: str | None = None


def sourcing_problems(pack: KycPack) -> list[str]:
    """Return every place where the 'no source, no claim' rule is broken."""
    ids = {s.id for s in pack.sources}
    problems: list[str] = []
    dupes = {s.id for s in pack.sources if [x.id for x in pack.sources].count(s.id) > 1}
    for d in sorted(dupes):
        problems.append(f"sources: duplicate id '{d}'")

    def check(path: str, sources: list[str], has_value: bool = True):
        if not has_value:
            return
        if not sources:
            problems.append(f"{path}: no source")
        for sid in sources:
            if sid not in ids:
                problems.append(f"{path}: unknown source id '{sid}'")

    for field in ("legal_name", "registration_number", "status", "address", "incorporation_date", "sector"):
        fact: SourcedValue = getattr(pack.company, field)
        check(f"company.{field}", fact.sources, fact.value not in (None, ""))
    for i, o in enumerate(pack.officers):
        check(f"officers[{i}] {o.name}", o.sources)
    for i, o in enumerate(pack.owners):
        check(f"owners[{i}] {o.name}", o.sources)
    for i, n in enumerate(pack.news):
        check(f"news[{i}] {n.title[:40]}", n.sources)
    if pack.financial_summary and any(v is not None for v in (pack.financial_summary.revenue, pack.financial_summary.net_profit)):
        check("financial_summary", pack.financial_summary.sources)
    for i, f in enumerate(pack.risk.factors):
        check(f"risk.factors[{i}] {f.factor}", f.sources)
    for i, f in enumerate(pack.flags):
        check(f"flags[{i}] {f.type}", f.sources)
    return problems


def json_schema() -> dict[str, Any]:
    return KycPack.model_json_schema()
