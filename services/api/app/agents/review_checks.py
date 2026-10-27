"""Deterministic reviewer checks that look beyond the pack's own consistency: policy coverage (what is missing), news
flags against news sources, whether the orchestrator merged the specialists' output unchanged, and notes from the
tool-call log. Each returns plain data; reviewer.py turns it into the feedback object."""
from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from kyc_common import KycPack
from kyc_common.schema import FinancialSummary, Flag, NewsItem, Officer, Owner

from .review_capture import RunCapture
from .risk_check import POLICY_PREFIX, Problem

SPECIALISTS = ("ownership", "news", "bank", "financials", "evaluator")
ACTIVE_STATUSES = {"active", "ativa", "ativo"}
SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}
# Pack field each specialist's output ends up in, for "this specialist never ran".
SPECIALIST_FIELD = {"ownership": "owners", "news": "news", "bank": "flags", "financials": "financial_summary",
                    "evaluator": "risk"}


@dataclass(frozen=True)
class Missing:
    path: str
    required_by: str
    expected: str
    found: str
    status: str          # absent | null_without_flag | unsupported
    owner: str


@dataclass(frozen=True)
class Note:
    about: str
    text: str
    based_on: str = ""


def _norm(value: object) -> str:
    return " ".join(str(value).split()).casefold()


# --- news flags must agree with news sources (policy section 5) -----------------------------------------------------

def news_flag_findings(pack: KycPack) -> list[Problem]:
    rule = f"{POLICY_PREFIX}5 adverse media"
    flags = [(i, f) for i, f in enumerate(pack.flags) if f.type == "adverse_media"]
    adverse = [(i, n) for i, n in enumerate(pack.news) if n.sentiment == "adverse"]
    news_ids = {sid for n in pack.news for sid in n.sources}
    problems: list[Problem] = []

    def covers(flag, item) -> bool:
        return not item.sources or bool(set(flag.sources) & set(item.sources))

    for i, flag in flags:
        if not adverse:
            problems.append(Problem(
                f"flags[{i}]: an adverse_media flag is raised but no article in `news` is adverse.", "flags",
                f"flags[{i}]", rule, "an adverse article in `news`", "none", cause="unsupported_claim"))
            continue
        if not set(flag.sources) & news_ids:
            # Both an adverse article and an adverse_media flag exist: only their cited source ids do not line up.
            # That is a sourcing slip worth a warning, not a reason to send the pack back.
            problems.append(Problem(
                f"flags[{i}]: the adverse_media flag cites no source that backs an article in `news`.", "flags",
                f"flags[{i}]", rule, f"one of the sources of an adverse article: {sorted(news_ids)}",
                ", ".join(flag.sources) or "no sources", cause="unsupported_claim", blocking=False))
            continue
        covered = [n for _, n in adverse if covers(flag, n)]
        want = max((n.severity for n in covered), key=lambda s: SEVERITY_RANK[s], default="none")
        if want in ("medium", "high") and flag.severity != want:
            problems.append(Problem(
                f"flags[{i}]: the adverse_media flag is '{flag.severity}' but the articles it cites are '{want}' "
                f"under policy section 5.", "flags", f"flags[{i}]", rule, want, flag.severity))
    for i, item in adverse:
        if item.severity == "none":
            problems.append(Problem(
                f"news[{i}]: the article is marked adverse but has severity 'none'; policy section 5 classes an "
                f"adverse article as medium or high.", "flags", f"news[{i}]", rule, "medium or high", "none"))
        if not any(covers(f, item) for _, f in flags):
            cited = sorted({sid for _, f in flags for sid in f.sources})
            problems.append(Problem(
                f"news[{i}]: the article is adverse but no adverse_media flag cites it.", "flags", f"news[{i}]", rule,
                f"an adverse_media flag citing {item.sources}", f"adverse_media flags cite {cited}" if flags else "no flag",
                cause="missing_data", blocking=not flags))   # a flag exists but cites other ids: a warning
    flag_types = {f.type for f in pack.flags}
    if (pack.risk.recommendation == "decline" and flags and not flag_types & {"inactive_company", "prohibited_sector"}
            and not any(n.severity == "high" for _, n in adverse)):
        problems.append(Problem(
            "risk.recommendation: 'decline' for adverse media needs a high-severity (sanctions-related) article, "
            "and the adverse articles here are not high severity.", "policy", "risk.recommendation",
            f"{POLICY_PREFIX}9 decline conditions", "a high-severity adverse article", "none", cause="unsupported_claim"))
    return problems


# --- policy coverage: what the pack should contain and does not ----------------------------------------------------

def coverage(pack: KycPack, capture: RunCapture | None, expect_all_specialists: bool) -> list[Missing]:
    items: list[Missing] = []
    has_missing_flag = any(f.type == "missing_data" for f in pack.flags)
    flag_index = {f.type: i for i, f in reversed(list(enumerate(pack.flags)))}

    for name in ("legal_name", "registration_number", "status", "address"):
        if getattr(pack.company, name).value in (None, "") and not has_missing_flag:
            items.append(Missing(f"company.{name}", f"{POLICY_PREFIX}3 customer identification",
                                 "a value from the register, or a missing_data flag",
                                 "null and no missing_data flag", "null_without_flag", "orchestrator"))
    status = pack.company.status.value
    inactive = status not in (None, "") and _norm(status) not in ACTIVE_STATUSES
    if inactive and "inactive_company" not in flag_index:
        items.append(Missing("flags", f"{POLICY_PREFIX}3 the company must be active", "an inactive_company flag",
                             f"status {status!r} and no inactive_company flag", "absent", "evaluator"))
    if "inactive_company" in flag_index and not inactive and status not in (None, ""):
        items.append(Missing(f"flags[{flag_index['inactive_company']}]", f"{POLICY_PREFIX}3 the company must be active",
                             "no inactive_company flag for an active company", f"status {status!r}", "unsupported",
                             "evaluator"))
    if not pack.officers and not has_missing_flag:
        items.append(Missing("officers", f"{POLICY_PREFIX}4 identify all directors",
                             "the register's officers, or a missing_data flag", "no officers and no missing_data flag",
                             "null_without_flag", "ownership"))
    corporate = any(o.type == "company" or o.via for o in pack.owners)
    if corporate and "complex_ownership" not in flag_index:
        items.append(Missing("flags", f"{POLICY_PREFIX}4 any corporate shareholder is complex ownership",
                             "a complex_ownership flag", "a corporate shareholder and no flag", "absent", "ownership"))
    if "complex_ownership" in flag_index and not corporate:
        items.append(Missing(f"flags[{flag_index['complex_ownership']}]",
                             f"{POLICY_PREFIX}4 any corporate shareholder is complex ownership",
                             "a corporate shareholder behind the flag", "none of the owners is a company", "unsupported",
                             "ownership"))
    if pack.financial_summary is None and not has_missing_flag:
        items.append(Missing("financial_summary", f"{POLICY_PREFIX}7 financial summary",
                             "the latest annual accounts, or a missing_data flag", "null and no missing_data flag",
                             "null_without_flag", "financials"))
    if capture is not None and expect_all_specialists:
        for name in SPECIALISTS:
            if name not in capture.runs:
                items.append(Missing(SPECIALIST_FIELD[name], "orchestrator workflow: every specialist runs",
                                     f"the {name} specialist's output", "the specialist was never run", "absent", name))
    return items


# --- merge fidelity: the orchestrator must take the specialists' output as returned ----------------------------------

def _merge_problem(path: str, what: str, agent: str, missing: set, extra: set) -> Problem:
    parts = []
    if missing:
        parts.append("left out: " + ", ".join(sorted(map(str, missing))))
    if extra:
        parts.append(f"not returned by it: " + ", ".join(sorted(map(str, extra))))
    return Problem(f"{path}: the final pack differs from what the {agent} specialist returned ({'; '.join(parts)}).",
                   "merge", path, f"orchestrator step 4: take {what} as returned", "the specialist's output unchanged",
                   "; ".join(parts), cause="merge_changed", produced_by="orchestrator", suggested_owner="orchestrator")


def _dicts(value: object) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def merge_findings(pack: KycPack, capture: RunCapture) -> list[Problem]:
    problems: list[Problem] = []
    out = capture.subagents

    if "ownership" in out:
        officers = {_norm(o["name"]) for o in _dicts(out["ownership"].get("officers")) if o.get("name")}
        final = {_norm(o.name) for o in pack.officers}
        if officers != final:
            problems.append(_merge_problem("officers", "officers", "ownership", officers - final, final - officers))
        owners = {(_norm(o["name"]), o.get("type"), bool(o.get("is_ultimate_beneficial_owner")))
                  for o in _dicts(out["ownership"].get("owners")) if o.get("name")}
        final_owners = {(_norm(o.name), o.type, o.is_ultimate_beneficial_owner) for o in pack.owners}
        if owners != final_owners:
            problems.append(_merge_problem("owners", "owners", "ownership", owners - final_owners,
                                           final_owners - owners))
    if "news" in out:
        # A specialist value outside the schema (sentiment 'negative', severity 'low') has to be translated by the
        # orchestrator, so only the article's presence is compared for those; reply_schema_findings reports the value.
        valid = {"sentiment": {"positive", "neutral", "adverse"}, "severity": {"none", "medium", "high"}}
        said = {}
        for n in _dicts(out["news"].get("news")):
            if n.get("url"):
                pair = (n.get("sentiment", "neutral"), n.get("severity", "none"))
                said[n["url"]] = pair if pair[0] in valid["sentiment"] and pair[1] in valid["severity"] else None
        final = {n.url: (n.sentiment, n.severity) for n in pack.news}
        changed = {f"{url} changed from {said[url]} to {final[url]}"
                   for url in said.keys() & final.keys() if said[url] is not None and said[url] != final[url]}
        if said.keys() != final.keys() or changed:
            problems.append(_merge_problem("news", "news", "news", set(said) - set(final),
                                           (set(final) - set(said)) | changed))
    if "financials" in out:
        sub = out["financials"].get("financial_summary")
        want = (sub.get("year"), sub.get("revenue"), sub.get("net_profit")) if isinstance(sub, dict) else None
        fs = pack.financial_summary
        got = (fs.year, fs.revenue, fs.net_profit) if fs else None
        if want != got:
            problems.append(Problem(
                f"financial_summary: the final pack has (year, revenue, net profit) {got} but the financials "
                f"specialist returned {want}.", "merge", "financial_summary",
                "orchestrator step 4: take financial_summary as returned", str(want), str(got),
                cause="merge_changed", produced_by="orchestrator", suggested_owner="orchestrator"))
    if "bank" in out:
        final_conflicts = [f for f in pack.flags if f.type == "conflicting_data"]
        final_messages = {_norm(f.message) for f in final_conflicts}
        for flag in _dicts(out["bank"].get("flags")):
            if flag.get("type") != "conflicting_data" or _norm(flag.get("message", "")) in final_messages:
                continue
            sources = set(flag.get("sources") or [])
            # The same difference, same sources, other words: reworded. No flag for it at all: dropped or merged.
            reworded = bool(sources) and any(set(f.sources) == sources for f in final_conflicts)
            verb = "reworded" if reworded else "missing"
            problems.append(Problem(
                f"flags: a conflicting_data flag from the bank specialist is {verb} in the final pack: "
                f"{str(flag.get('message', ''))[:160]!r}", "merge", "flags",
                "orchestrator step 5: never drop or merge a conflicting_data flag from the bank specialist",
                str(flag.get("message", ""))[:300],
                "; ".join(f.message[:150] for f in final_conflicts)[:300] or "no conflicting_data flag in the pack",
                cause="merge_changed", blocking=not reworded, produced_by="orchestrator",
                suggested_owner="orchestrator"))
    if "evaluator" in out:
        risk = out["evaluator"].get("risk")
        if isinstance(risk, dict):
            for field_name in ("rating", "recommendation"):
                if risk.get(field_name) != getattr(pack.risk, field_name):
                    problems.append(Problem(
                        f"risk.{field_name}: the final pack says '{getattr(pack.risk, field_name)}' but the evaluator "
                        f"returned '{risk.get(field_name)}'.", "merge", f"risk.{field_name}",
                        "orchestrator step 5: take the evaluator's risk as returned", str(risk.get(field_name)),
                        str(getattr(pack.risk, field_name)), cause="merge_changed", produced_by="orchestrator",
                        suggested_owner="orchestrator"))
        by_id = {s.id: s for s in pack.sources}
        for src in _dicts(out["evaluator"].get("sources")):
            if not str(src.get("reference", "")).startswith(POLICY_PREFIX) or not src.get("excerpt"):
                continue
            final = by_id.get(src.get("id"))
            if final is None:
                problems.append(Problem(
                    f"sources: the evaluator's policy source '{src.get('id')}' is not in the final pack.", "merge",
                    f"sources[{src.get('id')}]", "orchestrator step 5: add the evaluator's sources to the pack",
                    "the source present", "missing", cause="merge_changed", produced_by="orchestrator",
                    suggested_owner="orchestrator"))
            elif _norm(final.excerpt or "") != _norm(src["excerpt"]):
                problems.append(Problem(
                    f"sources: the excerpt of '{final.id}' was changed after the evaluator returned it.", "merge",
                    f"sources[{final.id}].excerpt", "the orchestrator must not retype the evaluator's policy excerpts",
                    src["excerpt"][:120], (final.excerpt or "")[:120], cause="merge_changed",
                    produced_by="orchestrator", suggested_owner="orchestrator"))
    return problems


REPLY_ITEMS = (("ownership", "officers", Officer), ("ownership", "owners", Owner), ("news", "news", NewsItem))


def reply_schema_findings(capture: RunCapture) -> list[Problem]:
    """Values a specialist returned that the pack schema does not allow (sentiment 'negative', severity 'low'). The
    orchestrator has to translate them, which is where incoherent items come from, so the specialist is named. Advisory:
    what reaches the pack is judged by the other checks. Extra keys are ignored."""
    items = [(agent, key, model, _dicts(capture.subagents.get(agent, {}).get(key))) for agent, key, model in REPLY_ITEMS]
    items += [(agent, "flags", Flag, _dicts(output.get("flags"))) for agent, output in capture.subagents.items()]
    summary = capture.subagents.get("financials", {}).get("financial_summary")
    if isinstance(summary, dict):
        items.append(("financials", "financial_summary", FinancialSummary, [summary]))
    problems = []
    for agent, key, model, entries in items:
        for i, entry in enumerate(entries):
            try:
                model.model_validate(entry)
            except ValidationError as e:
                for err in e.errors():
                    if err["type"] == "extra_forbidden":
                        continue
                    path = f"{agent}.{key}[{i}]." + ".".join(str(x) for x in err["loc"])
                    problems.append(Problem(
                        f"{path}: the {agent} specialist returned a value the pack schema does not allow "
                        f"({err['msg']}).", "schema", path, "specialist replies must use the KYC pack schema's values",
                        err["type"], str(err.get("input"))[:60], blocking=True, produced_by=agent,  # TEMP: test send-back to specialist
                        suggested_owner=agent))
    return problems


def flag_producers(pack: KycPack, capture: RunCapture) -> dict[str, str]:
    """Which specialist each final flag came from, by matching type and message; no match means the orchestrator."""
    seen = {}
    for name, output in capture.subagents.items():
        for flag in _dicts(output.get("flags")):
            seen.setdefault((flag.get("type"), _norm(flag.get("message", ""))), name)
    return {f"flags[{i}]": seen.get((f.type, _norm(f.message)), "orchestrator") for i, f in enumerate(pack.flags)}


# --- unrequested changes: a human revision must change only what was asked --------------------------------------------

REGRESSION_RULE = "human send-back: change only what was asked"
# Sections compared against the previous pack. A change nobody asked for blocks in the first four; the others start as
# advisory (promote them once real diffs show it is safe). `sources`, and the source-id lists inside items, may change.
BLOCKING_SECTIONS = ("company", "officers", "owners", "financial_summary")
ADVISORY_SECTIONS = ("news", "flags", "risk")
# Evidence that feeds the risk score: if one of these legitimately changed, `risk` may change with it.
RISK_INPUTS = ("company", "owners", "news", "flags", "financial_summary")
ANY_SECTION = "*"   # in `requested`: the human's request could not be mapped to a section, so nothing can be judged


def _canon(value: object) -> object:
    """A form to compare: key and list order ignored, source-id lists dropped."""
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in sorted(value.items()) if k != "sources"}
    if isinstance(value, list):
        return sorted((_canon(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    return value


def _describe_change(old: object, new: object) -> str:
    if isinstance(old, list) and isinstance(new, list):
        dump = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)  # noqa: E731
        old_items, new_items = {dump(v) for v in old}, {dump(v) for v in new}
        parts = [f"{label} {len(items)}: " + " | ".join(i[:70] for i in sorted(items)[:2])
                 for label, items in (("removed", old_items - new_items), ("added", new_items - old_items)) if items]
        return "; ".join(parts)
    if isinstance(old, dict) and isinstance(new, dict):
        return "differs in " + ", ".join(sorted(k for k in old.keys() | new.keys() if old.get(k) != new.get(k)))
    return f"{str(old)[:70]} -> {str(new)[:70]}"


def unrequested_changes(previous: KycPack, pack: KycPack, requested: set[str]) -> list[Problem]:
    """What changed between the previous pack and the new one that nobody asked for. `requested` holds the top-level
    pack fields the human's triaged points (and earlier send-backs in this run) name, or ANY_SECTION."""
    if ANY_SECTION in requested:
        return []
    before, after = previous.model_dump(mode="json"), pack.model_dump(mode="json")
    changed = {s: (before[s], after[s]) for s in (*BLOCKING_SECTIONS, *ADVISORY_SECTIONS)
               if _canon(before[s]) != _canon(after[s])}
    allowed = set(requested)
    if any(s in RISK_INPUTS for s in changed if s in allowed):
        allowed.add("risk")
    problems = []
    for section, (old, new) in changed.items():
        if section in allowed:
            continue
        problems.append(Problem(
            f"{section}: changed but nothing in the human's comment asked for it ({_describe_change(old, new)}). "
            f"Restore it exactly as in the previous pack.", "merge", section, REGRESSION_RULE,
            "unchanged from the previous pack", _describe_change(old, new)[:300], cause="merge_changed",
            blocking=section in BLOCKING_SECTIONS, produced_by="orchestrator", suggested_owner="orchestrator"))
    return problems


# --- notes from the tool-call log: advisory only -------------------------------------------------------------------

def log_notes(capture: RunCapture) -> list[Note]:
    notes = [Note(f"tool error: {c.tool}",
                  f"{c.tool} (called by {c.agent}) returned an error. If the pack depends on it, a flag should say "
                  f"the source could not be checked.", c.input[:120])
             for c in capture.tool_calls if c.is_error]
    notes += [Note(f"{name} reply", f"The {name} specialist's reply was not a JSON object, so what the orchestrator "
                                    f"merged from it could not be compared.", text[:120])
              for name, text in capture.unparsed.items()]
    return notes
