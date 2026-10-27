"""
Run your pipeline for ONE company from the terminal, without the web app or the database.
Prints progress, checks the pack against the schema and the sourcing rule, and scores it if the
company is in the test set. The fastest way to iterate on prompts and agents.

  docker compose exec api python -m app.cli "Hawksmoor Meridian Textiles" UK
  docker compose exec api python -m app.cli --company-id BR-012
  docker compose exec api python -m app.cli "Tejo Engenharia" PT --out /tmp/pack.json
  docker compose exec api python -m app.cli --company-id UK-001 --compare-out /tmp/UK-001.md

Simulate a human sending the pack back (Day 5):
  docker compose exec api python -m app.cli --company-id PT-014 --out /tmp/v1.json
  docker compose exec api python -m app.cli --company-id PT-014 --previous /tmp/v1.json \\
      --feedback "The UBOs behind the holding company are missing"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

from kyc_common import KycPack, sourcing_problems

from .context import PipelineContext
from .pipeline import run_pipeline
from .settings import settings


def _comparison_md(answer: dict, pack: KycPack, result: dict) -> str:
    """Markdown table of expected (answers.json) vs. actual pack values, with the per-field score."""
    exp, c, fs = answer["expected"], pack.company, pack.financial_summary
    fin = exp["financials"]
    fields = result["fields"]
    owners = [o.name for o in pack.owners if o.is_ultimate_beneficial_owner and o.type == "person"]
    rows = [
        ("legal_name", exp["legal_name"], c.legal_name.value),
        ("registration_number", exp["registration_number"], c.registration_number.value),
        ("status", exp["status"], c.status.value),
        ("address", exp["address"], c.address.value),
        ("directors", exp["directors"], [o.name for o in pack.officers if "secretar" not in o.role.lower()]),
        ("beneficial_owners", exp["ultimate_beneficial_owners"], owners),
        ("adverse_news", exp["adverse_news_urls"], [n.url for n in pack.news if n.sentiment == "adverse"]),
        ("financials", fin, fs and {"year": fs.year, "currency": fs.currency, "revenue": fs.revenue,
                                    "net_profit": fs.net_profit}),
        ("risk_rating", exp["risk_rating"], pack.risk.rating),
        ("recommendation", exp["recommendation"], pack.risk.recommendation),
        ("flags", exp["flags"], sorted({f.type for f in pack.flags})),
    ]

    def cell(v) -> str:
        return str(json.dumps(v, default=str) if isinstance(v, (list, dict)) else v).replace("|", "\\|")

    lines = [f"# {answer['company_id']}: {answer['input']['name']} ({answer['input']['country']})", "",
             f"Score: {result['score']:.0%} | difficulty: {answer['difficulty']} | cases: {', '.join(answer['cases']) or '-'}",
             "", "| field | score | expected | actual |", "|---|---|---|---|"]
    for name, e, a in rows:
        sc = fields.get(name, fields.get("beneficial_owners" if name == "beneficial_owners" else name))
        lines.append(f"| {name} | {'' if sc is None else format(sc, '.0%')} | {cell(e)} | {cell(a)} |")
    lines += ["", f"Schema: {fields.get('schema', 0):.0%} | Sourcing: {fields.get('sourcing', 0):.0%}"]
    if result["notes"]:
        lines += ["", "Notes:", *[f"- {n}" for n in result["notes"]]]
    lines += ["", "Flag messages:", *[f"- [{f.severity}] {f.type}: {f.message}" for f in pack.flags]]
    return "\n".join(lines) + "\n"


def _scorer():
    """Import the scoring tool (mounted at /srv/scoring in Docker, or ../../../scoring locally)."""
    here = Path(__file__).resolve()
    for candidate in [Path("/srv/scoring"), *[p / "scoring" for p in here.parents]]:
        if (candidate / "score.py").exists():
            sys.path.insert(0, str(candidate))
            import score  # type: ignore
            return score
    return None


async def _main(args: argparse.Namespace) -> int:
    score = _scorer()
    answer = None
    if score:
        answers = score.load_answers()
        if args.company_id:
            answer = next((a for a in answers if a["company_id"] == args.company_id), None)
            if not answer:
                print(f"Unknown company id {args.company_id}", file=sys.stderr)
                return 2
            args.name, args.country = answer["input"]["name"], answer["input"]["country"]
        else:
            answer = next((a for a in answers if a["input"]["name"].lower() == args.name.lower()
                           and a["input"]["country"] == args.country), None)
    if not args.name or not args.country:
        print("Give a company name and country, or --company-id", file=sys.stderr)
        return 2

    metrics: dict = {}
    started = time.time()

    async def emit(event: dict):
        if event.get("type") == "metrics":
            for k, v in (event.get("data") or {}).items():
                metrics[k] = metrics.get(k, 0) + v if isinstance(v, (int, float)) else v
            return
        print(f"  [{time.time() - started:6.1f}s] {event.get('step') or ''}: {event.get('message')}", file=sys.stderr)

    previous = KycPack.model_validate_json(Path(args.previous).read_text()) if args.previous else None
    ctx = PipelineContext(run_id=f"cli-{uuid.uuid4().hex[:8]}", company_name=args.name, country=args.country,
                          version=2 if args.feedback else 1, feedback=args.feedback, previous_pack=previous,
                          settings=settings, _emit=emit)
    print(f"Running pipeline for {args.name} ({args.country})", file=sys.stderr)
    result = await run_pipeline(ctx)
    pack = result if isinstance(result, KycPack) else KycPack.model_validate(result)
    elapsed = time.time() - started

    problems = sourcing_problems(pack)
    print(f"\nDone in {elapsed:.1f}s. Risk {pack.risk.rating}, recommendation {pack.risk.recommendation}, "
          f"{len(pack.flags)} flag(s), {len(pack.sources)} source(s).", file=sys.stderr)
    if metrics:
        print(f"Usage: {metrics}", file=sys.stderr)
    print(f"Sourcing: {'OK' if not problems else str(len(problems)) + ' problem(s)'}", file=sys.stderr)
    for p in problems[:10]:
        print(f"  - {p}", file=sys.stderr)

    if answer:
        r = score.score_pack(answer, json.loads(pack.model_dump_json()), metrics={**metrics, "latency_s": elapsed})
        print(f"\nScore for {answer['company_id']} ({answer['difficulty']}{', ' + ', '.join(answer['cases']) if answer['cases'] else ''}): {r['score']:.0%}", file=sys.stderr)
        for f, v in r["fields"].items():
            print(f"  {'✓' if v == 1 else '✗'} {f:<20} {v:.0%}", file=sys.stderr)
        for n in r["notes"]:
            print(f"  note: {n}", file=sys.stderr)
        if args.compare_out:
            Path(args.compare_out).write_text(_comparison_md(answer, pack, r))
            print(f"\nExpected-vs-actual comparison written to {args.compare_out}", file=sys.stderr)
    else:
        print("\n(Not in the test set, so no score.)", file=sys.stderr)
        if args.compare_out:
            print("--compare-out skipped: no expected answer for this company.", file=sys.stderr)

    out = pack.model_dump_json(indent=2)
    if args.out:
        Path(args.out).write_text(out)
        print(f"\nPack written to {args.out}", file=sys.stderr)
    elif args.json:
        print(out)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the pipeline for one company and score it.")
    ap.add_argument("name", nargs="?", help="Company name, as an analyst would type it")
    ap.add_argument("country", nargs="?", choices=["UK", "PT", "BR"])
    ap.add_argument("--company-id", help="Test-set id instead of name and country, e.g. UK-007")
    ap.add_argument("--json", action="store_true", help="Print the pack JSON to stdout")
    ap.add_argument("--out", help="Write the pack JSON to a file")
    ap.add_argument("--compare-out", help="Write a Markdown table of expected (answers.json) vs. actual values")
    ap.add_argument("--feedback", help="Reviewer comments, as if the pack had been sent back")
    ap.add_argument("--previous", help="Pack JSON file the feedback refers to")
    sys.exit(asyncio.run(_main(ap.parse_args())))


if __name__ == "__main__":
    main()
