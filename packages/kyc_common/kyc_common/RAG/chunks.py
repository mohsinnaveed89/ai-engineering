import json, os, re
from functools import cache
from pathlib import Path

# DATA_DIR is /data in the container; locally fall back to <repo>/data relative to this file
DATA_DIR = Path(os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[3] / "data")
DOCS = DATA_DIR / "documents"

@cache
def index():
    """doc path -> index.json entry. Read on first use so importing this module needs no /data (the MCP server only searches)."""
    return {d["path"]: d for d in json.load(open(DOCS / "index.json"))["documents"]}

UNIT = re.compile(r"(?:£'000|milhares de|R\$ mil)")      # "figures in thousands" markers

KPI_HEAD = re.compile(r"(?mi)^### (?:key performance indicators|principais indicadores|destaques financeiros)\s*$")

def kpi_values(part):
    """KPI table as {label: {year: raw cell}}, e.g. {"Turnover": {"2025": "£10,236", "2024": "£10,974"}}; {} if no KPI section."""
    m = KPI_HEAD.search(part)
    if not m:
        return {}
    body = re.split(r"(?m)^#{1,3} ", part[m.end():], maxsplit=1)[0]   # stop at the next heading
    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in body.splitlines() if l.lstrip().startswith("|")]
    if len(rows) < 3:
        return {}
    header = rows[0]                                                   # ['', '2025', '2024']
    return {r[0]: {h: r[i] for i, h in enumerate(header) if i} for r in rows[2:] if r[0]}   # rows[1] is the separator

def chunk_by_section(text):
    return re.split(r"(?m)^(?=## )", text)                            # keep the "## " line

def chunk_document(rel_path):
    meta = index()[rel_path]
    parts = chunk_by_section((DOCS / rel_path).read_text())
    is_report = meta["type"] == "annual_report"
    if is_report:
        parts  = [parts[0] + parts[1]] + parts[2:]
    out = []
    for i, part in enumerate(parts):
        first = part.split("\n", 1)[0]
        chunk = {
            "text": part.strip(),
            "company": meta.get("company_name"),          # None for policies
            "country": meta.get("country"),
            "year": meta.get("year"),
            "language": meta["language"],
            "section": "Cover" if is_report and i == 0 else first.lstrip("# ").strip(),
            "doc_id": meta["doc_id"],
            "consolidated": meta.get("consolidated", False),
            "unit": 1000 if UNIT.search(part) else 1,                   # see next section
        }
        kpis = kpi_values(part)
        if kpis:
            chunk["kpis"] = kpis                          # raw cells, unit/locale left as-is
        out.append(chunk)
    return out

if __name__ == "__main__":
    for p in ("annual_reports/uk/AR-UK-99551.md", "annual_reports/pt/AR-PT-22704.md", "policies/kyc_policy.md"):
        print(p, [( c.get("kpis")) for c in chunk_document(p)])