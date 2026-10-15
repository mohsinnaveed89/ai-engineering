"""
Company data generator for the AI Academy starter kit.

Generates a deterministic set of FICTIONAL companies for the UK, Portugal and
Brazil, plus everything the mock data API and scoring tool need:

  data/generated/uk_register.json   Companies House style register
  data/generated/pt_register.json   Portuguese commercial register style
  data/generated/br_register.json   Receita Federal (CNPJ) style register
  data/generated/news.json          News articles (English and Portuguese)
  data/generated/bank.json          Internal bank systems
  data/generated/companies.json     The test set: what an analyst would type
  data/generated/answers.json       The correct answer for every company
  data/documents/...                Annual-report extracts and KYC policies

Every company and person is fictional. Any resemblance to a real company or
person is a coincidence.

Usage:
    python data/generator/generate.py            # default seed and size
    python data/generator/generate.py --seed 7 --per-country 80
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "generated"
DOCS = ROOT / "data" / "documents"
TEMPLATES = Path(__file__).resolve().parent / "templates"

TODAY = date(2026, 10, 1)
REPORT_YEAR = 2025

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------
SECTORS = {
    # key: (risk class, UK word, PT word, BR word, UK SIC, PT CAE, BR CNAE, description EN, description PT)
    "logistics": ("standard", "Logistics", "Logística", "Logística", "49410", "49410", "4930-2/02", "Freight transport by road", "Transportes rodoviários de mercadorias"),
    "food": ("standard", "Foods", "Alimentar", "Alimentos", "10890", "10893", "1099-6/99", "Manufacture of food products", "Fabricação de produtos alimentares"),
    "engineering": ("standard", "Engineering", "Engenharia", "Engenharia", "71129", "71120", "7112-0/00", "Engineering activities", "Atividades de engenharia"),
    "software": ("standard", "Software", "Software", "Software", "62012", "62010", "6201-5/01", "Software development", "Desenvolvimento de software"),
    "pharma": ("standard", "Pharma", "Farmacêutica", "Farma", "21200", "21201", "2121-1/01", "Manufacture of pharmaceutical preparations", "Fabricação de medicamentos"),
    "textiles": ("standard", "Textiles", "Têxteis", "Têxtil", "13921", "13920", "1351-1/00", "Manufacture of textiles", "Fabricação de têxteis"),
    "energy": ("standard", "Energy", "Energia", "Energia", "35110", "35112", "3511-5/01", "Production of electricity", "Produção de eletricidade"),
    "construction": ("standard", "Construction", "Construções", "Construtora", "41201", "41200", "4120-4/00", "Construction of buildings", "Construção de edifícios"),
    "media": ("standard", "Media", "Media", "Mídia", "59111", "59110", "5911-1/99", "Media production", "Produção de conteúdos"),
    "money_services": ("high_risk", "Exchange", "Câmbios", "Câmbio", "66190", "66190", "6619-3/99", "Money services and currency exchange", "Serviços de câmbio e pagamentos"),
    "digital_assets": ("high_risk", "Digital Assets", "Ativos Digitais", "Ativos Digitais", "64999", "64991", "6619-3/99", "Digital asset trading", "Negociação de ativos digitais"),
    "gambling": ("prohibited", "Gaming", "Jogos", "Apostas", "92000", "92000", "9200-3/99", "Gambling and betting", "Jogos de fortuna e azar e apostas"),
}
STANDARD_SECTORS = [k for k, v in SECTORS.items() if v[0] == "standard"]

NAME_STEMS = {
    "UK": ["Albion", "Ashford", "Blackwater", "Bramley", "Calder", "Carrick", "Davenport", "Elmstead", "Fenwick",
           "Greystone", "Harrowgate", "Hollins", "Kestrel", "Langley", "Marlow", "Northcote", "Oakridge", "Penrose",
           "Redcliffe", "Stanmore", "Thornbury", "Whitlock", "Yarrow", "Ambleside", "Brockley", "Cheviot", "Dunmore",
           "Fairholme", "Glenrothes", "Hawksmoor", "Ivybridge", "Kingsmere", "Lowther", "Millbrook", "Netherby"],
    "PT": ["Atlântica", "Barroso", "Costa Verde", "Douro", "Estrela", "Ferreira", "Galeão", "Lusitânia", "Mondego",
           "Nogueira", "Tejo", "Alvorada", "Ribeira", "Serra Alta", "Sado", "Vouga", "Minhoto", "Algarvia", "Arrábida",
           "Bairrada", "Caramulo", "Dão Vivo", "Espinhaço", "Foz Nova", "Gerês", "Lezíria", "Marão", "Ourique",
           "Portela", "Quinta Nova", "Tâmega", "Zêzere", "Alcoa", "Belém Norte", "Cávado"],
    "BR": ["Aurora", "Bandeirantes", "Cerrado", "Guanabara", "Ipê Roxo", "Jacarandá", "Pantanal", "Tropeiro",
           "Vale Azul", "Serra Gaúcha", "Itapuã", "Sertão Novo", "Araucária", "Buriti", "Caatinga", "Carnaúba",
           "Iguaçu", "Jequitibá", "Mantiqueira", "Paranapanema", "Piracema", "Tapajós", "Xingu Verde", "Mandacaru",
           "Pitanga", "Juriti", "Capivari", "Maracanã Sul", "Ubatuba", "Anhumas", "Taquari", "Jatobá", "Coqueiral",
           "Sabiá", "Urupema"],
}
MIDS = {
    "UK": ["", "", "", "Lumen", "Vector", "Harbour", "Meridian", "Beacon", "Summit", "Crest", "Anchor"],
    "PT": ["", "", "", "Nova", "Global", "Ibérica", "Prime", "Lux"],
    "BR": ["", "", "", "Brasil", "Nova", "Prime", "Sul", "Norte"],
}

FIRST = {
    "UK": ["James", "Oliver", "Harry", "George", "Thomas", "William", "Amelia", "Olivia", "Isla", "Emily", "Sophie",
           "Charlotte", "Daniel", "Matthew", "Rachel", "Hannah", "Graham", "Fiona", "Alistair", "Priya", "Arjun",
           "Niamh", "Callum", "Eleanor", "Rhys", "Bethan", "Imogen", "Samuel", "Zara", "Owen"],
    "PT": ["João", "Pedro", "Miguel", "Tiago", "Rui", "Nuno", "Ricardo", "Maria", "Ana", "Inês", "Beatriz", "Sofia",
           "Catarina", "Mariana", "Joana", "Filipa", "Diogo", "Gonçalo", "Teresa", "Helena", "Bruno", "Rita",
           "Duarte", "Leonor", "Vasco", "Marta", "André", "Carolina", "Hugo", "Raquel"],
    "BR": ["Lucas", "Gabriel", "Rafael", "Mateus", "Felipe", "Gustavo", "Bruno", "Juliana", "Fernanda", "Camila",
           "Larissa", "Beatriz", "Aline", "Patrícia", "Renata", "Thiago", "Rodrigo", "Vinícius", "Letícia", "Mariana",
           "Carlos", "Paulo", "Luana", "Débora", "Caio", "Isabela", "Otávio", "Natália", "Henrique", "Bianca"],
}
LAST = {
    "UK": ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Robinson", "Wright",
           "Thompson", "Evans", "Walker", "White", "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke", "Patel",
           "Khan", "Lewis", "Harrison", "Morgan", "Kennedy", "Fraser", "Lloyd", "Hughes", "Ellison"],
    "PT": ["Silva", "Santos", "Ferreira", "Pereira", "Oliveira", "Costa", "Rodrigues", "Martins", "Sousa",
           "Fernandes", "Gonçalves", "Gomes", "Lopes", "Marques", "Alves", "Almeida", "Ribeiro", "Pinto", "Carvalho",
           "Teixeira", "Moreira", "Correia", "Mendes", "Nunes", "Soares", "Vieira", "Monteiro", "Cardoso", "Rocha",
           "Neves"],
    "BR": ["Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves", "Pereira", "Lima", "Gomes",
           "Costa", "Ribeiro", "Martins", "Carvalho", "Almeida", "Lopes", "Soares", "Fernandes", "Vieira", "Barbosa",
           "Rocha", "Dias", "Nascimento", "Andrade", "Moreira", "Nunes", "Marques", "Machado", "Mendes", "Freitas"],
}
CITIES = {
    "UK": [("London", "EC2A", "England"), ("Manchester", "M1", "England"), ("Leeds", "LS1", "England"),
           ("Bristol", "BS1", "England"), ("Birmingham", "B3", "England"), ("Edinburgh", "EH2", "Scotland"),
           ("Glasgow", "G2", "Scotland"), ("Cardiff", "CF10", "Wales"), ("Newcastle upon Tyne", "NE1", "England"),
           ("Belfast", "BT1", "Northern Ireland")],
    "PT": [("Lisboa", "1250", "Lisboa"), ("Porto", "4000", "Porto"), ("Braga", "4700", "Braga"),
           ("Coimbra", "3000", "Coimbra"), ("Aveiro", "3800", "Aveiro"), ("Faro", "8000", "Faro"),
           ("Setúbal", "2900", "Setúbal"), ("Leiria", "2400", "Leiria"), ("Viseu", "3500", "Viseu"),
           ("Guimarães", "4800", "Braga")],
    "BR": [("São Paulo", "01310", "SP"), ("Rio de Janeiro", "20040", "RJ"), ("Belo Horizonte", "30130", "MG"),
           ("Curitiba", "80020", "PR"), ("Porto Alegre", "90010", "RS"), ("Florianópolis", "88010", "SC"),
           ("Recife", "50030", "PE"), ("Salvador", "40020", "BA"), ("Campinas", "13010", "SP"),
           ("Goiânia", "74003", "GO")],
}
STREETS = {
    "UK": ["High Street", "Station Road", "King Street", "Victoria Road", "Church Lane", "Mill Lane", "Park Road",
           "Queen Street", "Market Street", "Wharf Road", "Bridge Street", "Castle Street"],
    "PT": ["Rua Augusta", "Avenida da Liberdade", "Rua de Santa Catarina", "Avenida da República", "Rua do Comércio",
           "Rua das Flores", "Avenida Central", "Rua Direita", "Largo do Rossio", "Rua da Boavista"],
    "BR": ["Avenida Paulista", "Rua XV de Novembro", "Avenida Brasil", "Rua da Consolação", "Avenida Atlântica",
           "Rua das Palmeiras", "Avenida Afonso Pena", "Rua Sete de Setembro", "Rua Augusta", "Avenida Ipiranga"],
}
OFFSHORE = [("British Virgin Islands", "BVI"), ("Panama", "PA"), ("Cayman Islands", "KY"), ("Seychelles", "SC")]
OUTLETS = {
    "UK": [("The City Ledger", "cityledger.example"), ("Northern Business Post", "nbpost.example"),
           ("Trade & Industry Weekly", "tiweekly.example"), ("The Financial Courier", "fincourier.example")],
    "PT": [("Jornal Económico do Tejo", "jetejo.example"), ("Diário dos Negócios", "diarionegocios.example"),
           ("Expresso Empresarial", "expresso-emp.example")],
    "BR": [("Folha do Comércio", "folhacomercio.example"), ("Gazeta Mercantil Paulista", "gazetamp.example"),
           ("Valor da Semana", "valorsemana.example"), ("Diário do Agronegócio", "diarioagro.example")],
}
AUDITORS = {
    "UK": ["Hartley & Moss LLP", "Crowther Lane Audit LLP", "Pemberton Fry LLP", "Ashcombe Partners LLP"],
    "PT": ["Martins, Sá & Associados, SROC", "Leal Auditores, SROC, Lda", "Rocha Ventura & Pares, SROC"],
    "BR": ["Ferraz Auditores Independentes S/S", "Moura & Lacerda Auditoria", "Prado Auditores Associados"],
}
CURRENCY = {"UK": "GBP", "PT": "EUR", "BR": "BRL"}
LANG = {"UK": "en", "PT": "pt", "BR": "pt"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def nipc(rng: random.Random) -> str:
    base = [5] + [rng.randint(0, 9) for _ in range(7)]
    total = sum(d * w for d, w in zip(base, range(9, 1, -1)))
    r = total % 11
    check = 0 if r < 2 else 11 - r
    return "".join(map(str, base)) + str(check)


def cnpj(rng: random.Random) -> str:
    base = [rng.randint(0, 9) for _ in range(8)] + [0, 0, 0, 1]

    def dv(nums, weights):
        s = sum(n * w for n, w in zip(nums, weights)) % 11
        return 0 if s < 2 else 11 - s

    d1 = dv(base, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = dv(base + [d1], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    digits = "".join(map(str, base + [d1, d2]))
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def uk_number(rng: random.Random, scottish: bool) -> str:
    return ("SC" + f"{rng.randint(100000, 999999)}") if scottish else f"{rng.randint(1, 16):02d}{rng.randint(0, 999999):06d}"


def fmt_money(amount: int, country: str) -> str:
    if country == "UK":
        return f"£{amount:,}"
    s = f"{amount:,}".replace(",", ".")
    return f"{s} €" if country == "PT" else f"R$ {s}"


@dataclass
class Person:
    first: str
    last: str
    country: str
    dob: date
    nationality: str

    @property
    def full(self) -> str:
        return f"{self.first} {self.last}"


@dataclass
class Company:
    cid: str
    country: str
    legal_name: str
    input_name: str
    reg_no: str
    sector: str
    status_raw: str
    active: bool
    incorporated: date
    address: dict | None
    officers: list = field(default_factory=list)          # [(Person, role, appointed)]
    shareholders: list = field(default_factory=list)      # [{"kind": "person"/"company", "ref": Person|Company|offshore dict, "pct": int}]
    cases: list = field(default_factory=list)
    trading_name: str | None = None
    financials: dict | None = None
    prior_financials: dict | None = None
    is_holding: bool = False
    news: list = field(default_factory=list)


class Gen:
    def __init__(self, seed: int, per_country: int):
        self.rng = random.Random(seed)
        self.per_country = per_country
        self.used_names: set[str] = set()
        self.companies: list[Company] = []
        self.holdings: list[Company] = []
        self.decoys: list[Company] = []
        self.articles: list[dict] = []
        self.counter = {"UK": 0, "PT": 0, "BR": 0}

    # ---------------- people, places ----------------
    def person(self, country: str) -> Person:
        r = self.rng
        nat = {"UK": "British", "PT": "Portuguesa", "BR": "Brasileira"}[country]
        dob = date(r.randint(1955, 1995), r.randint(1, 12), r.randint(1, 28))
        last = r.choice(LAST[country])
        if country in ("PT", "BR") and r.random() < 0.6:
            first_surname = r.choice([x for x in LAST[country] if x != last])
            last = f"{first_surname} {last}"
        return Person(r.choice(FIRST[country]), last, country, dob, nat)

    def address(self, country: str) -> dict:
        r = self.rng
        city, pc, region = r.choice(CITIES[country])
        street = r.choice(STREETS[country])
        num = r.randint(1, 240)
        if country == "UK":
            postcode = f"{pc} {r.randint(1, 9)}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}{r.choice('ABDEFGHJLNPQRSTUWXYZ')}"
            return {"line1": f"{num} {street}", "city": city, "postcode": postcode, "region": region, "country": "United Kingdom"}
        if country == "PT":
            postcode = f"{pc}-{r.randint(100, 999)}"
            return {"line1": f"{street}, {num}", "city": city, "postcode": postcode, "region": region, "country": "Portugal"}
        postcode = f"{pc}-{r.randint(100, 999)}"
        bairro = r.choice(["Centro", "Jardins", "Bela Vista", "Boa Viagem", "Savassi", "Moinhos de Vento", "Batel"])
        return {"line1": f"{street}, {num}", "district": bairro, "city": city, "postcode": postcode, "region": region, "country": "Brasil"}

    # ---------------- names ----------------
    def company_name(self, country: str, sector: str, holding: bool = False) -> tuple[str, str, str | None]:
        r = self.rng
        for _ in range(500):
            stem = r.choice(NAME_STEMS[country])
            mid = r.choice(MIDS[country])
            if holding:
                word = {"UK": "Holdings", "PT": "SGPS", "BR": "Participações"}[country]
            else:
                word = SECTORS[sector][{"UK": 1, "PT": 2, "BR": 3}[country]]
            base = " ".join(x for x in [stem, mid, word] if x)
            key = strip_accents(base).lower()
            if key in self.used_names:
                continue
            self.used_names.add(key)
            if country == "UK":
                suffix = r.choice(["Ltd", "Limited", "Ltd", "PLC"]) if not holding else "Limited"
                return f"{base} {suffix}", base, None
            if country == "PT":
                suffix = r.choice([", Lda", ", Lda", ", S.A."]) if not holding else ", S.A."
                if holding:
                    legal = f"{stem} {mid + ' ' if mid else ''}SGPS, S.A."
                else:
                    legal = f"{base}{suffix}"
                return legal, base, None
            suffix = r.choice(["LTDA", "LTDA", "S.A."]) if not holding else "LTDA"
            legal = strip_accents(f"{base} {suffix}").upper()
            trading = f"{stem} {SECTORS[sector][3] if not holding else 'Participações'}"
            return legal, trading, trading
        raise RuntimeError("ran out of names")

    def reg_no(self, country: str, address: dict | None = None) -> str:
        if country == "UK":
            return uk_number(self.rng, bool(address) and address.get("region") == "Scotland")
        if country == "PT":
            return nipc(self.rng)
        return cnpj(self.rng)

    # ---------------- financials ----------------
    def financials(self, country: str, year: int, scale: float | None = None, loss: bool = False) -> dict:
        r = self.rng
        scale = scale or r.choice([0.6, 1, 2, 5, 12, 30, 80])
        mult = {"UK": 1, "PT": 1.15, "BR": 5.5}[country]
        revenue = int(r.randint(800_000, 3_000_000) * scale * mult) // 1000 * 1000
        margin = r.uniform(-0.12, -0.02) if loss else r.uniform(0.02, 0.14)
        net = int(revenue * margin) // 1000 * 1000
        assets = int(revenue * r.uniform(0.6, 1.8)) // 1000 * 1000
        employees = max(4, int(revenue / r.randint(70_000, 160_000) / mult))
        return {"year": year, "currency": CURRENCY[country], "revenue": revenue, "net_profit": net,
                "total_assets": assets, "employees": employees, "auditor": r.choice(AUDITORS[country]),
                "going_concern_uncertainty": loss and r.random() < 0.5}

    # ---------------- build ----------------
    def make_company(self, country: str, cases: list[str]) -> Company:
        r = self.rng
        self.counter[country] += 1
        cid = f"{country}-{self.counter[country]:03d}"
        if "prohibited_sector" in cases:
            sector = "gambling"
        elif "high_risk_sector" in cases:
            sector = r.choice(["money_services", "digital_assets"])
        else:
            sector = r.choice(STANDARD_SECTORS)
        legal, input_name, trading = self.company_name(country, sector)
        if country == "UK" and r.random() < 0.3:
            input_name = legal  # sometimes analysts type the full legal name
        inactive = "inactive" in cases
        status_raw = {
            "UK": ("active" if not inactive else r.choice(["dissolved", "liquidation"])),
            "PT": ("ativa" if not inactive else r.choice(["dissolvida", "insolvente"])),
            "BR": ("ATIVA" if not inactive else r.choice(["BAIXADA", "INAPTA"])),
        }[country]
        incorporated = date(r.randint(1985, 2021), r.randint(1, 12), r.randint(1, 28))
        addr = self.address(country)
        c = Company(cid, country, legal, input_name, self.reg_no(country, addr), sector, status_raw, not inactive,
                    incorporated, addr, cases=list(cases), trading_name=trading)
        # officers
        for i in range(r.randint(1, 3)):
            p = self.person(country)
            role = {"UK": "director", "PT": r.choice(["Gerente", "Administrador"]), "BR": "Sócio-Administrador"}[country]
            appointed = incorporated + timedelta(days=r.randint(0, 3000))
            c.officers.append((p, role, min(appointed, TODAY - timedelta(days=30))))
        if country == "UK" and r.random() < 0.35:
            c.officers.append((self.person(country), "secretary", incorporated + timedelta(days=r.randint(0, 2000))))
        # shareholders
        if "holding_chain" in cases:
            offshore = "offshore" in cases
            holding = self.make_holding(country, offshore=offshore)
            pct = r.choice([100, 100, 80, 75])
            c.shareholders.append({"kind": "company", "ref": holding, "pct": pct})
            if pct < 100:
                c.shareholders.append({"kind": "person", "ref": c.officers[0][0], "pct": 100 - pct})
        else:
            directors = [o[0] for o in c.officers if o[1] != "secretary"]
            splits = r.choice([[100], [60, 40], [50, 50], [70, 20, 10], [40, 35, 25]])
            for idx, pct in enumerate(splits):
                p = directors[idx] if idx < len(directors) else self.person(country)
                c.shareholders.append({"kind": "person", "ref": p, "pct": pct})
        # financials
        loss = r.random() < 0.15
        if "no_annual_report" not in cases:
            c.financials = self.financials(country, REPORT_YEAR, loss=loss)
            if r.random() < 0.4:
                c.prior_financials = self.financials(country, REPORT_YEAR - 1, scale=None)
        if "missing_address" in cases:
            c.address = None
        return c

    def make_holding(self, country: str, offshore: bool) -> Company:
        r = self.rng
        cid = f"{country}-H{sum(1 for h in self.holdings if h.country == country) + 1:02d}"
        legal, input_name, trading = self.company_name(country, "engineering", holding=True)
        status = {"UK": "active", "PT": "ativa", "BR": "ATIVA"}[country]
        haddr = self.address(country)
        h = Company(cid, country, legal, input_name, self.reg_no(country, haddr), "holding", status, True,
                    date(r.randint(1995, 2018), r.randint(1, 12), r.randint(1, 28)), haddr,
                    is_holding=True, trading_name=trading)
        h.officers.append((self.person(country), {"UK": "director", "PT": "Administrador", "BR": "Administrador"}[country], h.incorporated))
        if offshore:
            jurisdiction, code = r.choice(OFFSHORE)
            stem = r.choice(["Orion", "Halcyon", "Sable", "Cobalt", "Vantage", "Meridian Bay", "Tidewater", "Kingfisher"])
            off = {"name": f"{stem} {r.choice(['Ventures', 'Capital', 'Investments', 'Trust'])} {r.choice(['Ltd', 'Inc', 'S.A.'])}",
                   "jurisdiction": jurisdiction, "registration_number": f"{code}-{r.randint(100000, 999999)}"}
            h.shareholders.append({"kind": "offshore", "ref": off, "pct": 100})
        else:
            splits = r.choice([[100], [50, 50], [60, 40]])
            h.shareholders.append({"kind": "person", "ref": h.officers[0][0], "pct": splits[0]})
            for pct in splits[1:]:
                h.shareholders.append({"kind": "person", "ref": self.person(country), "pct": pct})
        self.holdings.append(h)
        return h

    def make_namesake(self, c: Company) -> Company:
        """A dissolved company with a near-identical name: the first search result may be the wrong one."""
        r = self.rng
        swap = {"UK": [(" Ltd", " Limited"), (" Limited", " Ltd"), (" PLC", " Ltd")],
                "PT": [(", Lda", ", S.A."), (", S.A.", ", Lda")],
                "BR": [(" LTDA", " S.A."), (" S.A.", " LTDA")]}[c.country]
        legal = c.legal_name
        for a, b in swap:
            if legal.endswith(a):
                legal = legal[: -len(a)] + b
                break
        status = {"UK": "dissolved", "PT": "dissolvida", "BR": "BAIXADA"}[c.country]
        addr = self.address(c.country)
        d = Company(f"{c.cid}-NS", c.country, legal, c.input_name, self.reg_no(c.country, addr), c.sector, status, False,
                    date(r.randint(1990, 2005), r.randint(1, 12), r.randint(1, 28)), addr, trading_name=c.trading_name)
        d.officers.append((self.person(c.country), {"UK": "director", "PT": "Gerente", "BR": "Sócio-Administrador"}[c.country], d.incorporated))
        d.shareholders.append({"kind": "person", "ref": d.officers[0][0], "pct": 100})
        self.decoys.append(d)
        return d

    def plan_cases(self) -> list[list[str]]:
        """Return the case list for each company in one country."""
        n = self.per_country
        hard = [
            ["missing_address"], ["namesake", "missing_address"], ["no_annual_report"], ["no_annual_report"],
            ["no_annual_report", "name_collision"],
            ["conflicting_data"], ["conflicting_data"], ["conflicting_data"], ["conflicting_data", "negative_news"],
            ["holding_chain"], ["holding_chain"], ["holding_chain"], ["holding_chain", "offshore"],
            ["holding_chain", "offshore"], ["holding_chain", "offshore", "negative_news"],
            ["negative_news"], ["negative_news"], ["negative_news"], ["negative_news", "sanctions"],
            ["negative_news", "sanctions"],
            ["inactive"], ["inactive"], ["inactive", "negative_news"],
            ["prohibited_sector"], ["prohibited_sector"],
            ["high_risk_sector"], ["high_risk_sector"], ["high_risk_sector", "holding_chain"],
            ["namesake"], ["namesake", "name_collision"],
        ]
        hard = hard[: max(0, n - max(8, n * 2 // 5))]
        plans = hard + [["straightforward"]] * (n - len(hard))
        self.rng.shuffle(plans)
        return plans

    def build(self):
        for country in ("UK", "PT", "BR"):
            for cases in self.plan_cases():
                self.companies.append(self.make_company(country, cases))
        for c in self.companies:
            self.make_news(c)
            if "namesake" in c.cases:
                self.make_namesake(c)
        self.make_bank()

    # ---------------- news ----------------
    def article(self, c: Company, kind: str, about_name: str | None = None) -> dict:
        r = self.rng
        country = c.country
        outlet, domain = r.choice(OUTLETS[country])
        name = about_name or (c.trading_name if c.trading_name and r.random() < 0.5 else c.input_name)
        days_ago = r.randint(10, 700)
        published = TODAY - timedelta(days=days_ago)
        lang = LANG[country]
        severity = None
        category = None
        if lang == "en":
            if kind == "positive":
                title, body = r.choice([
                    (f"{name} wins multi-year contract", f"{name} has secured a multi-year supply agreement, its chief executive said on Tuesday. The company expects the deal to support hiring at its {c.address['city'] if c.address else 'main'} site."),
                    (f"{name} reports steady growth", f"{name} said trading remained in line with expectations, with order books up on the previous year. Management highlighted investment in new equipment."),
                    (f"{name} opens new facility", f"{name} has opened a new facility, creating around {r.randint(10, 80)} jobs. The company described the investment as a long-term commitment to the region."),
                ])
            elif kind == "neutral":
                title, body = r.choice([
                    (f"{name} appoints new finance director", f"{name} announced the appointment of a new finance director, effective next month. The previous holder of the role is retiring."),
                    (f"{name} attends industry trade fair", f"Representatives of {name} presented the company's latest services at an industry trade fair in Birmingham this week."),
                ])
            elif kind == "adverse_medium":
                category = r.choice(["lawsuit", "regulatory_fine", "labour_dispute"])
                severity = "medium"
                title, body = {
                    "lawsuit": (f"Supplier sues {name} over unpaid invoices", f"A former supplier has filed a claim against {name} in the High Court, alleging unpaid invoices of around £{r.randint(200, 900)}k. {name} said it would defend the claim."),
                    "regulatory_fine": (f"{name} fined by regulator", f"{name} has been fined £{r.randint(40, 400)}k by a regulator over record-keeping failures. The company said it had since strengthened its controls."),
                    "labour_dispute": (f"Workers at {name} vote to strike", f"Staff at {name} have voted to strike in a dispute over pay. The company said talks with the union were continuing."),
                }[category]
            else:  # adverse_high
                category = "sanctions" if "sanctions" in c.cases else r.choice(["fraud_investigation", "money_laundering"])
                severity = "high"
                title, body = {
                    "fraud_investigation": (f"Police investigate {name} over alleged invoice fraud", f"Police have opened an investigation into alleged invoice fraud at {name}. Two people have been interviewed under caution. {name} said it was cooperating fully."),
                    "money_laundering": (f"{name} named in money laundering probe", f"{name} has been named in a money laundering investigation led by the National Crime Agency, according to court documents seen by this newspaper."),
                    "sanctions": (f"{name} linked to sanctioned entity", f"Documents reviewed by this newspaper show that {name} made repeated payments to a company designated under UK financial sanctions. {name} declined to comment."),
                }[category]
        else:
            br = country == "BR"
            if kind == "positive":
                title, body = r.choice([
                    (f"{name} fecha contrato plurianual", f"A {name} assinou um contrato plurianual de fornecimento, segundo o seu presidente executivo. A empresa prevê reforçar a equipa na unidade de {c.address['city'] if c.address else 'origem'}."),
                    (f"{name} regista crescimento sólido" if not br else f"{name} registra crescimento sólido", f"A {name} afirmou que a atividade se manteve dentro das expectativas, com a carteira de encomendas em alta face ao ano anterior." if not br else f"A {name} informou que os resultados vieram dentro do esperado, com carteira de pedidos maior que no ano anterior."),
                ])
            elif kind == "neutral":
                title, body = r.choice([
                    (f"{name} nomeia novo diretor financeiro", f"A {name} anunciou a nomeação de um novo diretor financeiro, com efeitos a partir do próximo mês." if not br else f"A {name} anunciou um novo diretor financeiro, que assume no próximo mês."),
                    (f"{name} participa em feira do setor" if not br else f"{name} participa de feira do setor", f"Representantes da {name} apresentaram os seus serviços numa feira do setor esta semana." if not br else f"Representantes da {name} apresentaram seus serviços em uma feira do setor nesta semana."),
                ])
            elif kind == "adverse_medium":
                category = r.choice(["lawsuit", "regulatory_fine", "labour_dispute"])
                severity = "medium"
                if not br:
                    title, body = {
                        "lawsuit": (f"Fornecedor processa {name} por faturas em atraso", f"Um antigo fornecedor intentou uma ação judicial contra a {name}, alegando faturas por pagar de cerca de {r.randint(150, 800)} mil euros. A empresa diz que vai contestar."),
                        "regulatory_fine": (f"{name} multada pelo regulador", f"A {name} foi multada em {r.randint(30, 300)} mil euros por falhas no registo de operações. A empresa afirma ter reforçado os controlos internos."),
                        "labour_dispute": (f"Trabalhadores da {name} anunciam greve", f"Os trabalhadores da {name} aprovaram uma greve por aumentos salariais. A administração diz que as negociações continuam."),
                    }[category]
                else:
                    title, body = {
                        "lawsuit": (f"Fornecedor processa {name} por dívida", f"Um ex-fornecedor entrou com ação contra a {name} cobrando cerca de R$ {r.randint(1, 9)} milhões em notas não pagas. A empresa disse que vai recorrer."),
                        "regulatory_fine": (f"{name} é multada por órgão regulador", f"A {name} recebeu multa de R$ {r.randint(200, 2000)} mil por falhas em registros obrigatórios. A empresa afirma que já corrigiu os processos."),
                        "labour_dispute": (f"Funcionários da {name} entram em greve", f"Funcionários da {name} cruzaram os braços em protesto por reajuste salarial. A empresa diz que segue negociando com o sindicato."),
                    }[category]
            else:
                category = "sanctions" if "sanctions" in c.cases else r.choice(["fraud_investigation", "money_laundering"])
                severity = "high"
                if not br:
                    title, body = {
                        "fraud_investigation": (f"Polícia Judiciária investiga {name} por suspeitas de fraude", f"A Polícia Judiciária realizou buscas nas instalações da {name} no âmbito de uma investigação por fraude fiscal qualificada. A empresa diz estar a colaborar com as autoridades."),
                        "money_laundering": (f"{name} visada em inquérito por branqueamento de capitais", f"A {name} é visada num inquérito do Ministério Público por suspeitas de branqueamento de capitais, segundo documentos consultados por este jornal."),
                        "sanctions": (f"{name} ligada a entidade sob sanções internacionais", f"Documentos consultados por este jornal mostram pagamentos da {name} a uma entidade sujeita a sanções financeiras da União Europeia. A empresa não comentou."),
                    }[category]
                else:
                    title, body = {
                        "fraud_investigation": (f"Polícia Federal investiga {name} por fraude em licitações", f"A Polícia Federal cumpriu mandados de busca na sede da {name} em operação que apura fraude em licitações. A empresa afirma colaborar com as investigações."),
                        "money_laundering": (f"{name} é alvo de investigação por lavagem de dinheiro", f"A {name} é investigada pelo Ministério Público por suspeita de lavagem de dinheiro, segundo documentos obtidos pela reportagem."),
                        "sanctions": (f"{name} teria ligação com entidade sancionada", f"Documentos obtidos pela reportagem indicam pagamentos da {name} a empresa incluída em lista de sanções internacionais. A empresa não se manifestou."),
                    }[category]
        if country == "BR" and r.random() < 0.35 and about_name is None:
            body += f" (CNPJ {c.reg_no})"
        aid = f"news-{len(self.articles) + 1:04d}"
        slug = strip_accents(title).lower().replace(" ", "-")
        slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-")[:70]
        art = {"id": aid, "title": title, "body": body, "source": outlet,
               "url": f"https://{domain}/{published.year}/{published.month:02d}/{slug}",
               "published_at": published.isoformat(), "language": lang, "country": country}
        self.articles.append(art)
        return {"article": art, "kind": kind, "severity": severity, "category": category}

    def make_news(self, c: Company):
        r = self.rng
        for _ in range(r.randint(0, 2)):
            c.news.append(self.article(c, r.choice(["positive", "neutral", "positive"])))
        if "negative_news" in c.cases:
            if "sanctions" in c.cases or r.random() < 0.45:
                c.news.append(self.article(c, "adverse_high"))
            else:
                c.news.append(self.article(c, "adverse_medium"))
            if r.random() < 0.4:
                c.news.append(self.article(c, "adverse_medium"))
        if "name_collision" in c.cases:
            # adverse news about a DIFFERENT company with a confusingly similar name: must not be attributed
            stem = c.input_name.split()[0]
            other = {"UK": f"{stem} Group Services", "PT": f"{stem} Imobiliária", "BR": f"{stem} Incorporadora"}[c.country]
            noise = self.article(c, "adverse_high", about_name=other)
            noise["noise_for"] = c.cid
            c.news.append({**noise, "kind": "noise"})

    # ---------------- bank ----------------
    def make_bank(self):
        r = self.rng
        self.prospects, self.relationships = [], []
        for i, c in enumerate(self.companies, 1):
            declared_reg = c.reg_no
            declared_addr = dict(c.address) if c.address else None
            declared_officers = [o[0].full for o in c.officers if o[1] != "secretary"]
            conflict = None
            if "conflicting_data" in c.cases:
                options = ["address", "officers"] + (["registration_number"] if c.country != "BR" else [])
                conflict = r.choice(options)
                if conflict == "registration_number":
                    declared_reg = self.reg_no(c.country)
                elif conflict == "address":
                    declared_addr = self.address(c.country)
                else:
                    declared_officers = declared_officers[:-1] + [self.person(c.country).full] if len(declared_officers) > 1 else [self.person(c.country).full]
            c.conflict = conflict
            self.prospects.append({
                "prospect_id": f"PRS-{2026}{i:04d}",
                "declared_name": c.trading_name if (c.country == "BR" and c.trading_name) else c.input_name,
                "country": c.country,
                "declared_registration_number": declared_reg,
                "declared_address": declared_addr,
                "declared_directors": declared_officers,
                "requested_products": r.sample(["current_account", "trade_finance", "fx", "payroll", "credit_line"], k=r.randint(1, 3)),
                "relationship_manager": f"{r.choice(FIRST[c.country])} {r.choice(LAST[c.country])}",
                "submitted_at": (TODAY - timedelta(days=r.randint(1, 20))).isoformat(),
                "application_status": "submitted",
            })
            if r.random() < 0.15:
                self.relationships.append({
                    "registration_number": c.reg_no, "country": c.country, "customer_since": date(r.randint(2010, 2024), r.randint(1, 12), 1).isoformat(),
                    "products": r.sample(["current_account", "savings", "card_acquiring", "fx"], k=r.randint(1, 2)),
                    "internal_risk_rating": r.choice(["low", "low", "medium"]),
                    "last_kyc_review": (TODAY - timedelta(days=r.randint(100, 900))).isoformat(),
                })
        for d in self.decoys:
            if d.country != "BR":
                continue
            self.prospects.append({
                "prospect_id": f"PRS-{2019}{len(self.prospects) + 1:04d}", "declared_name": d.trading_name, "country": "BR",
                "declared_registration_number": d.reg_no, "declared_address": d.address,
                "declared_directors": [o[0].full for o in d.officers], "requested_products": ["current_account"],
                "relationship_manager": f"{r.choice(FIRST['BR'])} {r.choice(LAST['BR'])}",
                "submitted_at": date(2019, r.randint(1, 12), r.randint(1, 28)).isoformat(), "application_status": "withdrawn"})
        r.shuffle(self.prospects)
        self.risk_appetite = {
            "version": "2026.2",
            "ubo_threshold_pct": 25,
            "prohibited_sectors": ["gambling", "arms_and_defence", "adult_entertainment"],
            "high_risk_sectors": ["money_services", "digital_assets"],
            "high_risk_jurisdictions": [o[0] for o in OFFSHORE],
            "sector_codes": {k: {"risk": v[0], "uk_sic": v[4], "pt_cae": v[5], "br_cnae": v[6], "description": v[7]} for k, v in SECTORS.items()},
            "notes": "Refer to the KYC policy for how these inputs combine into a risk rating.",
        }


# --------------------------------------------------------------------------
# Expected answers (the KYC policy applied deterministically)
# --------------------------------------------------------------------------
def expected(c: Company) -> dict:
    flags = set()
    points = 0
    adverse = [n for n in c.news if n["kind"] in ("adverse_medium", "adverse_high")]
    if any(n["severity"] == "high" for n in adverse):
        points += 3
        flags.add("adverse_media")
    elif adverse:
        points += 1
        flags.add("adverse_media")
    risk = SECTORS[c.sector][0]
    if risk == "high_risk":
        points += 2
        flags.add("high_risk_sector")
    if risk == "prohibited":
        flags.add("prohibited_sector")
    ubos, ubo_identified = ultimate_owners(c)
    corporate = any(s["kind"] != "person" for s in c.shareholders)
    if corporate:
        points += 1
        flags.add("complex_ownership")
    if "offshore" in c.cases:
        points += 2
        flags.add("high_risk_jurisdiction")
    if not ubo_identified:
        points += 2
        flags.add("ubo_unidentified")
    if getattr(c, "conflict", None):
        points += 1
        flags.add("conflicting_data")
    if c.address is None or c.financials is None:
        points += 1
        flags.add("missing_data")
    if not c.active:
        flags.add("inactive_company")
    rating = "low" if points <= 1 else ("medium" if points <= 3 else "high")
    sanctions = any(n.get("category") == "sanctions" and n["kind"] == "adverse_high" for n in c.news)
    if not c.active or risk == "prohibited" or sanctions:
        rec = "decline"
    elif rating == "low":
        rec = "approve"
    else:
        rec = "investigate_further"
    fin = None
    if c.financials:
        f = c.financials
        fin = {"year": f["year"], "currency": f["currency"], "revenue": f["revenue"], "net_profit": f["net_profit"]}
    return {
        "legal_name": c.legal_name,
        "registration_number": c.reg_no,
        "status": "active" if c.active else "inactive",
        "status_raw": c.status_raw,
        "address": canonical_address(c.address),
        "directors": sorted({o[0].full for o in c.officers if o[1] != "secretary"}),
        "direct_owners": sorted({owner_name(s) for s in c.shareholders}),
        "ultimate_beneficial_owners": sorted(ubos),
        "ubo_identified": ubo_identified,
        "relevant_news_urls": sorted(n["article"]["url"] for n in c.news if n["kind"] != "noise"),
        "adverse_news_urls": sorted(n["article"]["url"] for n in adverse),
        "noise_news_urls": sorted(n["article"]["url"] for n in c.news if n["kind"] == "noise"),
        "financials": fin,
        "risk_points": points,
        "risk_rating": rating,
        "recommendation": rec,
        "flags": sorted(flags),
    }


def owner_name(s: dict) -> str:
    if s["kind"] == "person":
        return s["ref"].full
    if s["kind"] == "company":
        return s["ref"].legal_name
    return s["ref"]["name"]


def effective_owners(c: Company, factor: float = 1.0) -> tuple[dict[str, float], float]:
    """Effective (look-through) ownership per person, plus the share held by unidentifiable offshore owners."""
    owners: dict[str, float] = {}
    unknown = 0.0
    for s in c.shareholders:
        share = s["pct"] * factor
        if s["kind"] == "person":
            owners[s["ref"].full] = owners.get(s["ref"].full, 0) + share
        elif s["kind"] == "company":
            sub, unk = effective_owners(s["ref"], share / 100)
            for k, v in sub.items():
                owners[k] = owners.get(k, 0) + v
            unknown += unk
        else:
            unknown += share
    return owners, unknown


def ultimate_owners(c: Company, threshold: float = 25.0) -> tuple[list[str], bool]:
    owners, unknown = effective_owners(c)
    return [n for n, v in owners.items() if v >= threshold], unknown < threshold


def canonical_address(a: dict | None) -> str | None:
    if not a:
        return None
    parts = [a["line1"], a.get("district"), a["city"], a["postcode"], a["country"]]
    return ", ".join(p for p in parts if p)


# --------------------------------------------------------------------------
# Writers: registers in each country's own shape
# --------------------------------------------------------------------------
def uk_record(c: Company) -> dict:
    a = c.address
    return {
        "company_number": c.reg_no,
        "company_name": c.legal_name.upper(),
        "company_status": c.status_raw,
        "type": "plc" if c.legal_name.endswith("PLC") else "ltd",
        "date_of_creation": c.incorporated.isoformat(),
        "sic_codes": [SECTORS[c.sector][4]] if c.sector in SECTORS else ["64209"],
        "registered_office_address": None if a is None else {
            "address_line_1": a["line1"], "locality": a["city"], "postal_code": a["postcode"], "country": a["region"]},
        "officers": [{
            "name": f"{p.last.upper()}, {p.first}", "officer_role": role, "appointed_on": appt.isoformat(),
            "date_of_birth": {"month": p.dob.month, "year": p.dob.year}, "nationality": p.nationality,
        } for p, role, appt in c.officers],
        "persons_with_significant_control": [uk_psc(s) for s in c.shareholders],
    }


def band(pct: int) -> str:
    if pct > 75:
        return "ownership-of-shares-75-to-100-percent"
    if pct > 50:
        return "ownership-of-shares-50-to-75-percent"
    if pct >= 25:
        return "ownership-of-shares-25-to-50-percent"
    return None


def uk_psc(s: dict) -> dict:
    # The real register only reports bands. This mock also gives the exact share so ownership
    # through holding companies can be calculated deterministically.
    natures = [b for b in [band(s["pct"])] if b]
    return {**_uk_psc(s, natures), "ownership_percentage": s["pct"]}


def _uk_psc(s: dict, natures: list) -> dict:
    if s["kind"] == "person":
        p = s["ref"]
        return {"kind": "individual-person-with-significant-control", "name": f"{p.first} {p.last}",
                "natures_of_control": natures, "nationality": p.nationality,
                "date_of_birth": {"month": p.dob.month, "year": p.dob.year}}
    if s["kind"] == "company":
        h = s["ref"]
        return {"kind": "corporate-entity-person-with-significant-control", "name": h.legal_name.upper(),
                "natures_of_control": natures,
                "identification": {"registration_number": h.reg_no, "country_registered": {"UK": "England", "PT": "Portugal", "BR": "Brazil"}[h.country]}}
    o = s["ref"]
    return {"kind": "corporate-entity-person-with-significant-control", "name": o["name"].upper(),
            "natures_of_control": natures,
            "identification": {"registration_number": o["registration_number"], "country_registered": o["jurisdiction"]}}


def pt_record(c: Company) -> dict:
    a = c.address
    socios = []
    for s in c.shareholders:
        if s["kind"] == "person":
            socios.append({"nome": s["ref"].full, "tipo": "pessoa singular", "nacionalidade": s["ref"].nationality, "participacao_pct": s["pct"]})
        elif s["kind"] == "company":
            socios.append({"nome": s["ref"].legal_name, "tipo": "pessoa coletiva", "nipc": s["ref"].reg_no, "pais": {"UK": "Reino Unido", "PT": "Portugal", "BR": "Brasil"}[s["ref"].country], "participacao_pct": s["pct"]})
        else:
            socios.append({"nome": s["ref"]["name"], "tipo": "pessoa coletiva", "numero_registo_estrangeiro": s["ref"]["registration_number"], "pais": s["ref"]["jurisdiction"], "participacao_pct": s["pct"]})
    return {
        "nipc": c.reg_no,
        "denominacao": c.legal_name,
        "forma_juridica": "Sociedade Anónima" if "S.A." in c.legal_name else "Sociedade por Quotas",
        "estado": c.status_raw,
        "data_constituicao": c.incorporated.isoformat(),
        "cae": {"codigo": SECTORS[c.sector][5], "descricao": SECTORS[c.sector][8]} if c.sector in SECTORS else {"codigo": "64202", "descricao": "Atividades das sociedades gestoras de participações sociais"},
        "sede": None if a is None else {"morada": a["line1"], "codigo_postal": a["postcode"], "localidade": a["city"], "distrito": a["region"]},
        "orgaos_sociais": [{"nome": p.full, "cargo": role, "data_designacao": appt.isoformat()} for p, role, appt in c.officers],
        "socios": socios,
    }


def mask_cpf(rng: random.Random) -> str:
    return f"***{rng.randint(100, 999)}{rng.randint(100, 999)}**"


def br_record(c: Company, rng: random.Random) -> dict:
    a = c.address
    qsa = []
    directors = {o[0].full for o in c.officers}
    for p, role, appt in c.officers:
        pct = next((s["pct"] for s in c.shareholders if s["kind"] == "person" and s["ref"] is p), None)
        qsa.append({"nome_socio": strip_accents(p.full).upper(), "qualificacao_socio": "49-Sócio-Administrador" if pct else "05-Administrador",
                    "cnpj_cpf_do_socio": mask_cpf(rng), "data_entrada_sociedade": appt.isoformat(), "percentual_capital_social": pct or 0, "pais": "BRASIL"})
    for s in c.shareholders:
        if s["kind"] == "person" and s["ref"].full not in directors:
            qsa.append({"nome_socio": strip_accents(s["ref"].full).upper(), "qualificacao_socio": "22-Sócio", "cnpj_cpf_do_socio": mask_cpf(rng),
                        "data_entrada_sociedade": c.incorporated.isoformat(), "percentual_capital_social": s["pct"], "pais": "BRASIL"})
        elif s["kind"] == "company":
            qsa.append({"nome_socio": s["ref"].legal_name, "qualificacao_socio": "22-Sócio", "cnpj_cpf_do_socio": s["ref"].reg_no,
                        "data_entrada_sociedade": c.incorporated.isoformat(), "percentual_capital_social": s["pct"], "pais": "BRASIL"})
        elif s["kind"] == "offshore":
            qsa.append({"nome_socio": s["ref"]["name"].upper(), "qualificacao_socio": "37-Sócio Pessoa Jurídica Domiciliado no Exterior", "cnpj_cpf_do_socio": None,
                        "data_entrada_sociedade": c.incorporated.isoformat(), "percentual_capital_social": s["pct"], "pais": s["ref"]["jurisdiction"].upper()})
    return {
        "cnpj": c.reg_no,
        "razao_social": c.legal_name,
        "nome_fantasia": (c.trading_name or "").upper(),
        "situacao_cadastral": c.status_raw,
        "data_inicio_atividade": c.incorporated.isoformat(),
        "natureza_juridica": "205-1 - Sociedade Anônima Fechada" if c.legal_name.endswith("S.A.") else "206-2 - Sociedade Empresária Limitada",
        "cnae_fiscal": {"codigo": SECTORS[c.sector][6], "descricao": SECTORS[c.sector][8]} if c.sector in SECTORS else {"codigo": "6462-0/00", "descricao": "Holdings de instituições não financeiras"},
        "logradouro": None if a is None else a["line1"].split(",")[0],
        "numero": None if a is None else a["line1"].split(",")[-1].strip(),
        "bairro": None if a is None else a.get("district"),
        "municipio": None if a is None else strip_accents(a["city"]).upper(),
        "uf": None if a is None else a["region"],
        "cep": None if a is None else a["postcode"],
        "qsa": qsa,
    }


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------
def report_md(c: Company, f: dict) -> str:
    cur = c.country
    m = lambda v: fmt_money(v, cur)
    if cur == "UK":
        gc = ("\n\n## Going concern\n\nThe directors have identified a material uncertainty that may cast significant doubt on the "
              "company's ability to continue as a going concern, related to the renewal of its banking facilities.") if f["going_concern_uncertainty"] else (
              "\n\n## Going concern\n\nThe directors have a reasonable expectation that the company has adequate resources to continue in operational existence for the foreseeable future.")
        return f"""# {c.legal_name}
## Annual Report and Financial Statements for the year ended 31 December {f['year']}

Company registration number: {c.reg_no}

## Strategic report

{c.legal_name} operates in {SECTORS.get(c.sector, ('', '', '', '', '', '', '', 'investment holding'))[7].lower()}. During {f['year']} the company served customers across the United Kingdom and employed an average of {f['employees']} people.

## Key financial highlights

| Measure | {f['year']} |
|---|---|
| Turnover | {m(f['revenue'])} |
| Profit/(loss) for the financial year | {m(f['net_profit'])} |
| Total assets | {m(f['total_assets'])} |
| Average number of employees | {f['employees']} |
{gc}

## Independent auditor's report

We have audited the financial statements of {c.legal_name}. In our opinion the financial statements give a true and fair view of the state of the company's affairs as at 31 December {f['year']}.

{f['auditor']}, Statutory Auditor
"""
    if cur == "PT":
        gc = ("\n\n## Continuidade\n\nO órgão de gestão identificou uma incerteza material que pode suscitar dúvidas significativas sobre a "
              "capacidade da empresa de prosseguir em continuidade, relacionada com a renegociação do financiamento bancário.") if f["going_concern_uncertainty"] else (
              "\n\n## Continuidade\n\nAs demonstrações financeiras foram preparadas no pressuposto da continuidade das operações.")
        return f"""# {c.legal_name}
## Relatório e Contas do exercício de {f['year']}

NIPC: {c.reg_no}

## Relatório de gestão

A {c.legal_name} desenvolve a sua atividade na área de {SECTORS.get(c.sector, ('',) * 8 + ('gestão de participações sociais',))[8].lower()}. Em {f['year']}, a empresa contou com uma média de {f['employees']} trabalhadores.

## Principais indicadores

| Indicador | {f['year']} |
|---|---|
| Volume de negócios | {m(f['revenue'])} |
| Resultado líquido do período | {m(f['net_profit'])} |
| Total do ativo | {m(f['total_assets'])} |
| Número médio de trabalhadores | {f['employees']} |
{gc}

## Certificação legal das contas

Auditámos as demonstrações financeiras anexas da {c.legal_name}. Em nossa opinião, as demonstrações financeiras apresentam de forma verdadeira e apropriada a posição financeira em 31 de dezembro de {f['year']}.

{f['auditor']}
"""
    gc = ("\n\n## Continuidade operacional\n\nA administração identificou incerteza relevante relacionada à continuidade operacional, "
          "em razão da renegociação de dívidas bancárias em andamento.") if f["going_concern_uncertainty"] else (
          "\n\n## Continuidade operacional\n\nAs demonstrações financeiras foram elaboradas com base no pressuposto de continuidade operacional.")
    return f"""# {c.legal_name}
## Demonstrações Financeiras do exercício de {f['year']}

CNPJ: {c.reg_no}

## Relatório da administração

A {c.trading_name or c.legal_name} atua no segmento de {SECTORS.get(c.sector, ('',) * 8 + ('participações societárias',))[8].lower()}. Em {f['year']}, a companhia contou com média de {f['employees']} colaboradores.

## Destaques financeiros

| Indicador | {f['year']} |
|---|---|
| Receita operacional líquida | {m(f['revenue'])} |
| Lucro (prejuízo) líquido do exercício | {m(f['net_profit'])} |
| Ativo total | {m(f['total_assets'])} |
| Número médio de colaboradores | {f['employees']} |
{gc}

## Relatório do auditor independente

Examinamos as demonstrações financeiras da {c.legal_name}. Em nossa opinião, as demonstrações apresentam adequadamente, em todos os aspectos relevantes, a posição patrimonial e financeira em 31 de dezembro de {f['year']}.

{f['auditor']}
"""


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--per-country", type=int, default=50)
    args = ap.parse_args()

    g = Gen(args.seed, args.per_country)
    g.build()
    rng = random.Random(args.seed + 1)

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for sub in ("annual_reports",):
        if (DOCS / sub).exists():
            shutil.rmtree(DOCS / sub)

    everyone = g.companies + g.holdings + g.decoys
    rng.shuffle(everyone)
    reg = {
        "UK": {"source": "Mock UK company register (Companies House style)", "companies": [uk_record(c) for c in everyone if c.country == "UK"]},
        "PT": {"source": "Registo comercial simulado (Portugal)", "entidades": [pt_record(c) for c in everyone if c.country == "PT"]},
        "BR": {"source": "Cadastro CNPJ simulado (Brasil)", "empresas": [br_record(c, rng) for c in everyone if c.country == "BR"]},
    }
    for k, v in reg.items():
        (OUT / f"{k.lower()}_register.json").write_text(json.dumps(v, ensure_ascii=False, indent=2))

    articles = sorted(g.articles, key=lambda a: a["published_at"], reverse=True)
    (OUT / "news.json").write_text(json.dumps({"articles": articles}, ensure_ascii=False, indent=2))
    (OUT / "bank.json").write_text(json.dumps({"prospects": g.prospects, "relationships": g.relationships, "risk_appetite": g.risk_appetite}, ensure_ascii=False, indent=2))

    test_set, answers = [], []
    for c in g.companies:
        difficulty = "straightforward" if c.cases == ["straightforward"] else "hard"
        test_set.append({"company_id": c.cid, "name": c.input_name, "country": c.country, "difficulty": difficulty})
        answers.append({"company_id": c.cid, "input": {"name": c.input_name, "country": c.country},
                        "difficulty": difficulty, "cases": [x for x in c.cases if x != "straightforward"], "expected": expected(c)})
    (OUT / "companies.json").write_text(json.dumps({"companies": test_set}, ensure_ascii=False, indent=2))
    (OUT / "answers.json").write_text(json.dumps({"seed": args.seed, "answers": answers}, ensure_ascii=False, indent=2))

    # documents
    index = []
    for c in everyone:
        for f in [c.financials, c.prior_financials]:
            if not f:
                continue
            folder = DOCS / "annual_reports" / c.country.lower()
            folder.mkdir(parents=True, exist_ok=True)
            doc_id = f"{c.cid}-AR{f['year']}"
            path = folder / f"{doc_id}.md"
            path.write_text(report_md(c, f))
            index.append({"doc_id": doc_id, "type": "annual_report", "country": c.country, "company_name": c.legal_name,
                          "registration_number": c.reg_no, "year": f["year"], "language": LANG[c.country],
                          "path": str(path.relative_to(DOCS))})
    for p in sorted((DOCS / "policies").glob("*.md")):
        index.append({"doc_id": p.stem, "type": "policy", "country": None, "company_name": None, "registration_number": None,
                      "year": None, "language": "pt" if p.stem.startswith("procedimentos") else "en", "path": str(p.relative_to(DOCS))})
    (DOCS / "index.json").write_text(json.dumps({"documents": index}, ensure_ascii=False, indent=2))

    n_hard = sum(1 for t in test_set if t["difficulty"] == "hard")
    print(f"Generated {len(g.companies)} companies ({len(g.companies) - n_hard} straightforward, {n_hard} with built-in challenges), "
          f"{len(g.holdings)} holding companies, {len(articles)} news articles, {len(index)} documents.")


if __name__ == "__main__":
    main()
