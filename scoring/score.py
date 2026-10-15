"""
Scoring tool: checks KYC packs against the schema, the sourcing rule and the correct answers.

Examples
  # Submit every company in the test set to the API, wait, then score (the full evaluation)
  python scoring/score.py --api http://localhost:8000 --run-all

  # Only the straightforward companies in one country (quick happy-path check)
  python scoring/score.py --api http://localhost:8000 --run-all --country UK --difficulty straightforward

  # Score the latest existing run for each company, without starting new runs
  python scoring/score.py --api http://localhost:8000 --latest

  # Score pack JSON files saved in a folder (file name = company_id.json, e.g. UK-001.json)
  python scoring/score.py --packs-dir my_packs/

  # Compare with an earlier report (prompt optimisation: one change at a time, measure the delta)
  python scoring/score.py --api http://localhost:8000 --run-all --label prompt-v2 --compare scoring/reports/report-...-baseline.json

Reports are written to scoring/reports/ as Markdown and JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

import httpx
from pydantic import ValidationError

from kyc_common import KycPack, sourcing_problems
from kyc_common.normalize import norm_company, norm_person, norm_reg, norm_status, similarity

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.getenv("DATA_DIR", ROOT / "data"))
REPORTS = Path(os.getenv("REPORTS_DIR", ROOT / "scoring" / "reports"))

FIELDS = ["schema", "sourcing", "legal_name", "registration_number", "status", "address", "directors",
          "beneficial_owners", "adverse_news", "financials", "risk_rating", "recommendation", "flags"]
DONE = {"awaiting_review", "approved", "declined", "failed"}


# ------------------------------------------------------------------ scoring
def f1(expected: set, got: set) -> float:
    if not expected and not got:
        return 1.0
    if not expected or not got:
        return 0.0
    tp = len(expected & got)
    if tp == 0:
        return 0.0
    p, r = tp / len(got), tp / len(expected)
    return 2 * p * r / (p + r)


def score_pack(answer: dict, raw: dict | None, error: str | None = None, metrics: dict | None = None) -> dict:
    exp = answer["expected"]
    out = {"company_id": answer["company_id"], "country": answer["input"]["country"], "difficulty": answer["difficulty"],
           "cases": answer["cases"], "fields": {}, "notes": [], "metrics": metrics or {}}
    if raw is None:
        out["fields"] = {f: 0.0 for f in FIELDS}
        out["notes"].append(error or "no pack")
        out["score"] = 0.0
        return out
    try:
        pack = KycPack.model_validate(raw)
    except ValidationError as e:
        out["fields"] = {f: 0.0 for f in FIELDS}
        out["notes"].append(f"schema: {e.error_count()} error(s), first: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
        out["score"] = 0.0
        return out
    s = out["fields"]
    s["schema"] = 1.0
    problems = sourcing_problems(pack)
    s["sourcing"] = 1.0 if not problems else 0.0
    if problems:
        out["notes"].append(f"sourcing: {len(problems)} problem(s), e.g. {problems[0]}")

    c = pack.company
    s["legal_name"] = float(norm_company(c.legal_name.value) == norm_company(exp["legal_name"]))
    s["registration_number"] = float(norm_reg(c.registration_number.value) == norm_reg(exp["registration_number"]))
    s["status"] = float(norm_status(c.status.value) == exp["status"])
    if exp["address"] is None:
        s["address"] = 1.0 if not c.address.value else 0.0
        if c.address.value:
            out["notes"].append("address: reported an address but the register has none (missing data)")
    else:
        s["address"] = 1.0 if similarity(c.address.value, exp["address"]) >= 0.6 else 0.0

    directors = {norm_person(o.name) for o in pack.officers if "secretar" not in o.role.lower()}
    s["directors"] = f1({norm_person(n) for n in exp["directors"]}, directors)

    ubos = {norm_person(o.name) for o in pack.owners if o.is_ultimate_beneficial_owner and o.type == "person"}
    exp_ubos = {norm_person(n) for n in exp["ultimate_beneficial_owners"]}
    s["beneficial_owners"] = f1(exp_ubos, ubos)
    if not exp["ubo_identified"]:
        flagged = any(f.type == "ubo_unidentified" for f in pack.flags)
        s["beneficial_owners"] = (s["beneficial_owners"] + float(flagged)) / 2 if exp_ubos else float(flagged)

    adverse = {n.url for n in pack.news if n.sentiment == "adverse"}
    exp_adverse, noise = set(exp["adverse_news_urls"]), set(exp["noise_news_urls"])
    s["adverse_news"] = f1(exp_adverse, adverse)
    if adverse & noise:
        out["notes"].append("adverse_news: attributed news about a different, similarly named company")

    fin = exp["financials"]
    fs = pack.financial_summary
    if fin is None:
        s["financials"] = 1.0 if (fs is None or fs.revenue is None) else 0.0
    elif fs is None or fs.revenue is None:
        s["financials"] = 0.0
    else:
        rev_ok = abs(float(fs.revenue) - fin["revenue"]) <= 0.01 * abs(fin["revenue"])
        np_ok = fs.net_profit is not None and abs(float(fs.net_profit) - fin["net_profit"]) <= 0.01 * max(abs(fin["net_profit"]), 1)
        s["financials"] = (float(rev_ok) + float(np_ok) + float(fs.year == fin["year"])) / 3

    s["risk_rating"] = float(pack.risk.rating == exp["risk_rating"])
    s["recommendation"] = float(pack.risk.recommendation == exp["recommendation"])
    s["flags"] = f1(set(exp["flags"]), {f.type for f in pack.flags if f.type != "other"})
    out["score"] = round(mean(s.values()), 4)
    return out


# ------------------------------------------------------------------ collecting packs
def load_answers() -> list[dict]:
    return json.loads((DATA / "generated" / "answers.json").read_text())["answers"]


async def run_all(api: str, answers: list[dict], concurrency: int, timeout: int) -> dict[str, tuple]:
    sem = asyncio.Semaphore(concurrency)
    results: dict[str, tuple] = {}
    started = time.time()

    async with httpx.AsyncClient(base_url=api.rstrip("/"), timeout=30) as client:
        async def one(a: dict):
            async with sem:
                r = await client.post("/runs", json={"company_name": a["input"]["name"], "country": a["input"]["country"]})
                r.raise_for_status()
                rid = r.json()["id"]
                deadline = time.time() + timeout
                while time.time() < deadline:
                    await asyncio.sleep(1.5)
                    run = (await client.get(f"/runs/{rid}")).json()
                    if run["status"] in DONE:
                        results[a["company_id"]] = (run.get("pack"), run.get("error"), run.get("metrics"))
                        print(f"  {a['company_id']:<7} {run['status']:<16} {time.time() - started:6.0f}s", flush=True)
                        return
                results[a["company_id"]] = (None, f"timed out after {timeout}s", None)
                print(f"  {a['company_id']:<7} timed out", flush=True)

        await asyncio.gather(*(one(a) for a in answers))
    return results


def latest(api: str, answers: list[dict]) -> dict[str, tuple]:
    out = {}
    with httpx.Client(base_url=api.rstrip("/"), timeout=30) as client:
        for a in answers:
            items = client.get("/runs", params={"company_name": a["input"]["name"], "country": a["input"]["country"], "limit": 1}).json()["items"]
            if not items:
                out[a["company_id"]] = (None, "no run found", None)
                continue
            run = client.get(f"/runs/{items[0]['id']}").json()
            out[a["company_id"]] = (run.get("pack"), run.get("error"), run.get("metrics"))
    return out


def from_dir(folder: Path, answers: list[dict]) -> dict[str, tuple]:
    out = {}
    for a in answers:
        p = folder / f"{a['company_id']}.json"
        out[a["company_id"]] = (json.loads(p.read_text()), None, None) if p.exists() else (None, f"missing {p.name}", None)
    return out


# ------------------------------------------------------------------ reporting
def summarise(rows: list[dict]) -> dict:
    def agg(subset):
        return {"companies": len(subset), "score": round(mean(r["score"] for r in subset), 4) if subset else 0,
                "fields": {f: round(mean(r["fields"].get(f, 0) for r in subset), 4) for f in FIELDS} if subset else {}}
    groups = defaultdict(list)
    for r in rows:
        groups[("country", r["country"])].append(r)
        groups[("difficulty", r["difficulty"])].append(r)
        for case in r["cases"] or ["straightforward"]:
            groups[("case", case)].append(r)
    happy = [r for r in rows if r["difficulty"] == "straightforward"]
    happy_ok = [r for r in happy if all(r["fields"].get(f) == 1.0 for f in ("schema", "sourcing", "legal_name", "registration_number", "status"))]
    lat = [r["metrics"]["latency_s"] for r in rows if isinstance(r["metrics"].get("latency_s"), (int, float))]
    cost = [r["metrics"]["cost_usd"] for r in rows if isinstance(r["metrics"].get("cost_usd"), (int, float))]
    toks = [r["metrics"].get("input_tokens", 0) + r["metrics"].get("output_tokens", 0) for r in rows
            if isinstance(r["metrics"].get("input_tokens"), (int, float))]
    usage = {"runs_with_latency": len(lat), "avg_latency_s": round(mean(lat), 2) if lat else None,
             "max_latency_s": max(lat) if lat else None,
             "runs_with_cost": len(cost), "total_cost_usd": round(sum(cost), 4) if cost else None,
             "avg_cost_usd": round(mean(cost), 4) if cost else None,
             "avg_tokens": round(mean(toks)) if toks else None}
    return {"overall": agg(rows),
            "by": {f"{k}:{v}": agg(g) for (k, v), g in sorted(groups.items())},
            "happy_path": {"straightforward_companies": len(happy), "correct_and_sourced_identity": len(happy_ok)},
            "usage": usage}


def compare(rows: list[dict], summary: dict, baseline_path: Path) -> dict:
    """Per-field and per-company deltas against an earlier JSON report."""
    base = json.loads(Path(baseline_path).read_text())
    ids = {r["company_id"] for r in rows}
    brows = [r for r in base["companies"] if r["company_id"] in ids]   # compare like with like
    bscore = {r["company_id"]: r["score"] for r in brows}
    common = [r for r in rows if r["company_id"] in bscore]
    if not common:
        return {"baseline": str(baseline_path), "error": "no companies in common"}
    b_overall = round(mean(r["score"] for r in brows), 4)
    a_overall = round(mean(r["score"] for r in common), 4)
    fields = {}
    for f in FIELDS:
        bf = round(mean(r["fields"].get(f, 0) for r in brows), 4)
        af = round(mean(r["fields"].get(f, 0) for r in common), 4)
        fields[f] = {"before": bf, "after": af, "delta": round(af - bf, 4)}
    return {
        "baseline": str(baseline_path), "companies_compared": len(common),
        "overall": {"before": b_overall, "after": a_overall, "delta": round(a_overall - b_overall, 4)},
        "fields": fields,
        "improved": sorted(r["company_id"] for r in common if r["score"] > bscore[r["company_id"]] + 1e-9),
        "regressed": sorted(r["company_id"] for r in common if r["score"] < bscore[r["company_id"]] - 1e-9),
        "usage_before": base["summary"].get("usage"), "usage_after": summary.get("usage"),
    }


def write_report(rows: list[dict], summary: dict, label: str, comparison: dict | None = None) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = REPORTS / f"report-{stamp}{('-' + label) if label else ''}"
    base.with_suffix(".json").write_text(json.dumps({"summary": summary, "comparison": comparison, "companies": rows}, indent=2))
    o = summary["overall"]
    lines = [f"# KYC scoring report ({stamp})", "",
             f"Companies scored: **{o['companies']}** · Overall score: **{o['score']:.1%}**", "",
             f"Happy path: {summary['happy_path']['correct_and_sourced_identity']} of {summary['happy_path']['straightforward_companies']} "
             "straightforward companies have a valid, fully sourced pack with the correct name, number and status.", "",
             "## Accuracy per field", "", "| Field | Score |", "|---|---|"]
    lines += [f"| {f} | {v:.1%} |" for f, v in o["fields"].items()]
    u = summary["usage"]
    lines += ["", "## Cost and latency", ""]
    if u["runs_with_latency"] or u["runs_with_cost"]:
        lines += [f"- Average latency: {u['avg_latency_s']} s (max {u['max_latency_s']} s) over {u['runs_with_latency']} runs",
                  f"- Cost: {('$' + format(u['total_cost_usd'], '.4f') + ' total, $' + format(u['avg_cost_usd'], '.4f') + ' per run') if u['runs_with_cost'] else 'not recorded (call ctx.record_metrics(cost_usd=...) in your pipeline)'}",
                  f"- Average tokens per run: {u['avg_tokens'] if u['avg_tokens'] is not None else 'not recorded'}"]
    else:
        lines += ["Not available (scoring from files, or the API did not return metrics)."]
    if comparison and "error" not in comparison:
        c = comparison
        lines += ["", f"## Compared with `{Path(c['baseline']).name}` ({c['companies_compared']} companies in both)", "",
                  f"Overall: {c['overall']['before']:.1%} → {c['overall']['after']:.1%} (**{c['overall']['delta']:+.1%}**)", "",
                  "| Field | Before | After | Change |", "|---|---|---|---|"]
        lines += [f"| {f} | {(d['before'] or 0):.1%} | {d['after']:.1%} | {d['delta']:+.1%} |" for f, d in c["fields"].items()]
        lines += ["", f"Improved companies ({len(c['improved'])}): {', '.join(c['improved']) or 'none'}",
                  "", f"Regressed companies ({len(c['regressed'])}): {', '.join(c['regressed']) or 'none'}"]
    lines += ["", "## By segment", "", "| Segment | Companies | Score |", "|---|---|---|"]
    lines += [f"| {k} | {g['companies']} | {g['score']:.1%} |" for k, g in summary["by"].items()]
    def fmt_metric(r, k, f):
        v = r["metrics"].get(k)
        return f.format(v) if isinstance(v, (int, float)) else ""
    lines += ["", "## Per company", "", "| Company | Country | Difficulty | Score | Latency (s) | Cost ($) | Notes |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['company_id']} | {r['country']} | {r['difficulty']}{(' (' + ', '.join(r['cases']) + ')') if r['cases'] else ''} | {r['score']:.0%} | "
              f"{fmt_metric(r, 'latency_s', '{:.1f}')} | {fmt_metric(r, 'cost_usd', '{:.4f}')} | {'; '.join(r['notes'])[:160]} |" for r in rows]
    base.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return base


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--run-all", action="store_true", help="submit every selected company to the API, then score")
    src.add_argument("--latest", action="store_true", help="score the latest existing run per company")
    src.add_argument("--packs-dir", type=Path, help="score pack JSON files named <company_id>.json")
    ap.add_argument("--api", default=os.getenv("API_URL", "http://localhost:8000"))
    ap.add_argument("--country", choices=["UK", "PT", "BR"])
    ap.add_argument("--difficulty", choices=["straightforward", "hard"])
    ap.add_argument("--company", action="append", help="company_id to include (repeatable)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=900, help="seconds to wait for each run")
    ap.add_argument("--label", default="", help="added to the report file name, e.g. 'baseline' or 'prompt-v2'")
    ap.add_argument("--compare", type=Path, help="an earlier JSON report to compare against")
    args = ap.parse_args()

    answers = load_answers()
    if args.country:
        answers = [a for a in answers if a["input"]["country"] == args.country]
    if args.difficulty:
        answers = [a for a in answers if a["difficulty"] == args.difficulty]
    if args.company:
        answers = [a for a in answers if a["company_id"] in set(args.company)]
    if not answers:
        sys.exit("No companies match the filters.")

    print(f"Scoring {len(answers)} companies")
    if args.run_all:
        packs = asyncio.run(run_all(args.api, answers, args.concurrency, args.timeout))
    elif args.latest:
        packs = latest(args.api, answers)
    else:
        packs = from_dir(args.packs_dir, answers)

    rows = [score_pack(a, *packs.get(a["company_id"], (None, "not run", None))) for a in answers]
    summary = summarise(rows)
    comparison = compare(rows, summary, args.compare) if args.compare else None
    path = write_report(rows, summary, args.label, comparison)
    o = summary["overall"]
    print(f"\nOverall score: {o['score']:.1%} across {o['companies']} companies")
    for f, v in o["fields"].items():
        print(f"  {f:<20} {v:6.1%}")
    hp = summary["happy_path"]
    print(f"\nHappy path: {hp['correct_and_sourced_identity']}/{hp['straightforward_companies']} straightforward companies with a valid, sourced, correct identity")
    u = summary["usage"]
    if u["runs_with_latency"]:
        print(f"Latency: {u['avg_latency_s']} s average, {u['max_latency_s']} s max")
    if u["runs_with_cost"]:
        print(f"Cost: ${u['total_cost_usd']:.4f} total, ${u['avg_cost_usd']:.4f} per run")
    if comparison and "error" not in comparison:
        print(f"Compared with {args.compare.name}: {comparison['overall']['delta']:+.1%} overall, "
              f"{len(comparison['improved'])} companies improved, {len(comparison['regressed'])} regressed")
    print(f"Report: {path.with_suffix('.md')}")


if __name__ == "__main__":
    main()
