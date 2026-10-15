"""
Text content for the generator: news articles and annual-report extracts.

The wording is deliberately varied so that classifying news and reading reports needs
judgement, not keyword matching:
  * "victim" and "cleared" stories use alarming words (fraud, sanctions, lawsuit) but are NOT
    adverse for the company;
  * some adverse stories use no obvious keyword at all;
  * reports vary their labels, units (units or thousands), comparatives and narrative.
"""
from __future__ import annotations

import random

# --------------------------------------------------------------------------------------------
# News templates: (title, body). Placeholders: {name} {city} {n} {k} {m}
# --------------------------------------------------------------------------------------------
NEWS = {
    "en": {
        "positive": [
            ("{name} wins multi-year contract", "{name} has secured a multi-year supply agreement, its chief executive said on Tuesday. The company expects the deal to support hiring at its {city} site."),
            ("{name} reports steady growth", "{name} said trading remained in line with expectations, with order books up on the previous year. Management highlighted investment in new equipment."),
            ("{name} opens new facility in {city}", "{name} has opened a new facility, creating around {n} jobs. The company described the investment as a long-term commitment to the region."),
            ("Apprenticeship scheme at {name} praised", "A local college has praised {name} for taking on {n} apprentices this year, the largest intake in the company's history."),
            ("{name} named supplier of the year", "{name} was named supplier of the year at an industry awards evening, recognising its delivery record and customer service."),
        ],
        "neutral": [
            ("{name} appoints new finance director", "{name} announced the appointment of a new finance director, effective next month. The previous holder of the role is retiring."),
            ("{name} attends industry trade fair", "Representatives of {name} presented the company's latest services at an industry trade fair this week."),
            ("{name} moves head office within {city}", "{name} is relocating its head office to larger premises in {city} to accommodate a growing team."),
            ("Chief executive of {name} joins trade body board", "The chief executive of {name} has joined the board of a national trade association for a three-year term."),
        ],
        "victim": [
            ("{name} warns customers about fraudsters impersonating staff", "{name} has warned customers about emails from fraudsters posing as members of its accounts team and asking for payment details to be changed. The company said its own systems were not compromised."),
            ("Gang jailed for defrauding {name}", "Three men were jailed on Friday for an invoice fraud that cost {name} about £{k}k. The court heard the company had reported the fraud to police as soon as it was discovered."),
            ("{name} wins High Court case against former supplier", "{name} has won its lawsuit against a former supplier that failed to deliver goods it had been paid for. The judge awarded the company £{k}k plus costs."),
            ("Regulator closes review of {name} with no further action", "A regulator has closed its review of {name}, finding no breaches and taking no further action. The company said it had cooperated fully throughout."),
            ("{name} on sanctions compliance: 'screening is everyone's job'", "Speaking at a conference on trade sanctions, the compliance lead at {name} described how the company screens suppliers against sanctions lists before signing contracts."),
        ],
        "adverse_medium": {
            "lawsuit": [
                ("Supplier sues {name} over unpaid invoices", "A former supplier has filed a claim against {name} in the High Court, alleging unpaid invoices of around £{k}k. {name} said it would defend the claim."),
                ("{name} faces tribunal claims from former staff", "Eleven former employees of {name} have brought employment tribunal claims alleging unpaid overtime. The company disputes the claims."),
            ],
            "regulatory_fine": [
                ("{name} fined by regulator", "{name} has been fined £{k}k by a regulator over record-keeping failures. The company said it had since strengthened its controls."),
                ("Watchdog orders {name} to pay penalty", "A watchdog has ordered {name} to pay a penalty after it repeatedly filed returns late. It is the second penalty in three years."),
            ],
            "labour_dispute": [
                ("Workers at {name} vote to strike", "Staff at {name} have voted to strike in a dispute over pay. The company said talks with the union were continuing."),
            ],
            "environmental": [
                ("Spill at {name} site prompts clean-up order", "Environmental officers have ordered {name} to clean up a spill of industrial waste near its {city} site. Residents complained of a strong smell for several days."),
                ("Customers left out of pocket as {name} misses deliveries", "Dozens of customers say they have been waiting months for orders paid to {name}. Trading standards officers have asked the company to explain the delays."),
            ],
        },
        "adverse_high": {
            "fraud_investigation": [
                ("Police investigate {name} over alleged invoice fraud", "Police have opened an investigation into alleged invoice fraud at {name}. Two people have been interviewed under caution. {name} said it was cooperating fully."),
                ("Directors of {name} questioned over missing client money", "Two directors of {name} have been questioned by detectives after client money held by the company could not be accounted for, according to people familiar with the matter."),
            ],
            "money_laundering": [
                ("{name} named in money laundering probe", "{name} has been named in a money laundering investigation led by the National Crime Agency, according to court documents seen by this newspaper."),
                ("Court freezes {name} bank accounts", "A judge has frozen bank accounts belonging to {name} at the request of investigators examining the origin of large cash deposits."),
            ],
            "bribery": [
                ("{name} accused of paying officials to win contracts", "Prosecutors allege that {name} paid officials in return for public contracts worth £{m}m. The company denies wrongdoing."),
            ],
            "sanctions": [
                ("{name} linked to sanctioned entity", "Documents reviewed by this newspaper show that {name} made repeated payments to a company designated under UK financial sanctions. {name} declined to comment."),
                ("Shipments by {name} reached designated buyer, records show", "Customs records indicate that goods exported by {name} ended up with a buyer on the UK sanctions list. {name} said it was reviewing the reports."),
            ],
        },
    },
    "pt": {
        "positive": [
            ("{name} fecha contrato plurianual", "A {name} assinou um contrato plurianual de fornecimento, segundo o seu presidente executivo. A empresa prevê reforçar a equipa na unidade de {city}."),
            ("{name} regista crescimento sólido", "A {name} afirmou que a atividade se manteve dentro das expectativas, com a carteira de encomendas em alta face ao ano anterior."),
            ("{name} inaugura nova unidade em {city}", "A {name} inaugurou uma nova unidade que vai criar cerca de {n} postos de trabalho."),
            ("{name} distinguida como PME Excelência", "A {name} foi distinguida com o estatuto PME Excelência, que reconhece a solidez financeira e o desempenho da empresa."),
        ],
        "neutral": [
            ("{name} nomeia novo diretor financeiro", "A {name} anunciou a nomeação de um novo diretor financeiro, com efeitos a partir do próximo mês."),
            ("{name} participa em feira do setor", "Representantes da {name} apresentaram os seus serviços numa feira do setor esta semana."),
            ("{name} muda sede dentro de {city}", "A {name} vai mudar a sede para instalações maiores em {city}, para acomodar o crescimento da equipa."),
        ],
        "victim": [
            ("{name} alerta clientes para burla com faturas falsas", "A {name} alertou os clientes para mensagens fraudulentas que se fazem passar pela empresa e pedem a alteração do IBAN para pagamentos. A empresa garante que os seus sistemas não foram comprometidos."),
            ("Tribunal condena grupo que burlou a {name}", "O tribunal condenou três arguidos por uma burla que causou à {name} um prejuízo de cerca de {k} mil euros. A empresa apresentou queixa logo que detetou o esquema."),
            ("{name} ganha ação contra antigo fornecedor", "A {name} venceu a ação judicial que interpôs contra um antigo fornecedor que não entregou mercadoria já paga. O tribunal condenou o fornecedor a pagar {k} mil euros."),
            ("Regulador arquiva processo contra a {name}", "O regulador arquivou o processo de averiguação à {name}, sem encontrar qualquer infração. A empresa diz ter colaborado em todas as fases."),
        ],
        "adverse_medium": {
            "lawsuit": [
                ("Fornecedor processa {name} por faturas em atraso", "Um antigo fornecedor intentou uma ação judicial contra a {name}, alegando faturas por pagar de cerca de {k} mil euros. A empresa diz que vai contestar."),
            ],
            "regulatory_fine": [
                ("{name} multada pelo regulador", "A {name} foi multada em {k} mil euros por falhas no registo de operações. A empresa afirma ter reforçado os controlos internos."),
                ("ASAE instaura processo de contraordenação à {name}", "A ASAE instaurou um processo de contraordenação à {name} depois de várias reclamações de clientes sobre encomendas não entregues."),
            ],
            "labour_dispute": [
                ("Trabalhadores da {name} anunciam greve", "Os trabalhadores da {name} aprovaram uma greve por aumentos salariais. A administração diz que as negociações continuam."),
            ],
            "environmental": [
                ("Derrame na unidade da {name} obriga a limpeza", "As autoridades ambientais ordenaram à {name} a limpeza de um derrame de resíduos industriais junto à unidade de {city}."),
            ],
        },
        "adverse_high": {
            "fraud_investigation": [
                ("Polícia Judiciária investiga {name} por suspeitas de fraude", "A Polícia Judiciária realizou buscas nas instalações da {name} no âmbito de uma investigação por fraude fiscal qualificada. A empresa diz estar a colaborar com as autoridades."),
                ("Administradores da {name} constituídos arguidos", "Dois administradores da {name} foram constituídos arguidos num inquérito sobre o desvio de dinheiro de clientes, segundo fonte próxima do processo."),
            ],
            "money_laundering": [
                ("{name} visada em inquérito por branqueamento de capitais", "A {name} é visada num inquérito do Ministério Público por suspeitas de branqueamento de capitais, segundo documentos consultados por este jornal."),
                ("Contas da {name} arrestadas pelo Ministério Público", "O Ministério Público pediu e obteve o arresto das contas bancárias da {name}, no âmbito de uma investigação à origem de depósitos em numerário."),
            ],
            "bribery": [
                ("{name} suspeita de corromper funcionários para ganhar concursos", "A acusação sustenta que a {name} pagou a funcionários públicos para vencer concursos no valor de {m} milhões de euros. A empresa nega."),
            ],
            "sanctions": [
                ("{name} ligada a entidade sob sanções internacionais", "Documentos consultados por este jornal mostram pagamentos da {name} a uma entidade sujeita a sanções financeiras da União Europeia. A empresa não comentou."),
            ],
        },
    },
    "br": {
        "positive": [
            ("{name} fecha contrato plurianual", "A {name} assinou um contrato plurianual de fornecimento. A empresa prevê contratar na unidade de {city}."),
            ("{name} registra crescimento sólido", "A {name} informou que os resultados vieram dentro do esperado, com carteira de pedidos maior que no ano anterior."),
            ("{name} inaugura fábrica em {city}", "A {name} inaugurou uma nova fábrica que deve gerar cerca de {n} empregos diretos."),
            ("{name} recebe selo de boas práticas", "A {name} recebeu um selo setorial de boas práticas de gestão e governança."),
        ],
        "neutral": [
            ("{name} anuncia novo diretor financeiro", "A {name} anunciou um novo diretor financeiro, que assume no próximo mês."),
            ("{name} participa de feira do setor", "Representantes da {name} apresentaram seus serviços em uma feira do setor nesta semana."),
            ("{name} muda sede em {city}", "A {name} vai transferir a sede para um prédio maior em {city}."),
        ],
        "victim": [
            ("{name} alerta clientes sobre golpe com boletos falsos", "A {name} alertou clientes sobre boletos falsos emitidos por golpistas que se passam pela empresa. Segundo a companhia, seus sistemas não foram invadidos."),
            ("Justiça condena quadrilha que aplicou golpe na {name}", "A Justiça condenou quatro pessoas por um golpe que causou prejuízo de R$ {k} mil à {name}, que registrou boletim de ocorrência assim que identificou a fraude."),
            ("{name} vence ação contra ex-fornecedor", "A {name} venceu na Justiça a ação contra um ex-fornecedor que não entregou mercadorias já pagas."),
            ("Órgão regulador arquiva investigação sobre a {name}", "O órgão regulador arquivou a apuração sobre a {name} por não encontrar irregularidades."),
        ],
        "adverse_medium": {
            "lawsuit": [
                ("Fornecedor processa {name} por dívida", "Um ex-fornecedor entrou com ação contra a {name} cobrando cerca de R$ {m} milhões em notas não pagas. A empresa disse que vai recorrer."),
            ],
            "regulatory_fine": [
                ("{name} é multada por órgão regulador", "A {name} recebeu multa de R$ {k} mil por falhas em registros obrigatórios. A empresa afirma que já corrigiu os processos."),
                ("Procon notifica {name} após reclamações", "O Procon notificou a {name} depois de centenas de reclamações de consumidores sobre pedidos não entregues."),
            ],
            "labour_dispute": [
                ("Funcionários da {name} entram em greve", "Funcionários da {name} cruzaram os braços em protesto por reajuste salarial. A empresa diz que segue negociando com o sindicato."),
            ],
            "environmental": [
                ("Vazamento em unidade da {name} gera multa ambiental", "O órgão ambiental autuou a {name} após vazamento de resíduos perto da unidade de {city}."),
            ],
        },
        "adverse_high": {
            "fraud_investigation": [
                ("Polícia Federal investiga {name} por fraude em licitações", "A Polícia Federal cumpriu mandados de busca na sede da {name} em operação que apura fraude em licitações. A empresa afirma colaborar com as investigações."),
                ("Sócios da {name} são alvo de operação", "Sócios da {name} foram alvo de mandados de busca em operação que apura desvio de recursos de clientes."),
            ],
            "money_laundering": [
                ("{name} é alvo de investigação por lavagem de dinheiro", "A {name} é investigada pelo Ministério Público por suspeita de lavagem de dinheiro, segundo documentos obtidos pela reportagem."),
                ("Justiça bloqueia contas da {name}", "A Justiça bloqueou contas bancárias da {name} a pedido do Ministério Público, que investiga a origem de depósitos em espécie."),
            ],
            "bribery": [
                ("{name} é acusada de pagar propina para vencer contratos", "O Ministério Público acusa a {name} de pagar propina a servidores para vencer contratos de R$ {m} milhões. A empresa nega."),
            ],
            "sanctions": [
                ("{name} teria ligação com entidade sancionada", "Documentos obtidos pela reportagem indicam pagamentos da {name} a empresa incluída em lista de sanções internacionais. A empresa não se manifestou."),
            ],
        },
    },
}


def news_lang(country: str) -> str:
    return {"UK": "en", "PT": "pt", "BR": "br"}[country]


# --------------------------------------------------------------------------------------------
# Annual reports
# --------------------------------------------------------------------------------------------
LABELS = {
    "UK": {"revenue": ["Turnover", "Revenue", "Sales"],
           "profit": ["Profit/(loss) for the financial year", "Profit/(loss) after tax", "Net profit/(loss)"],
           "assets": ["Total assets", "Total assets"], "employees": ["Average number of employees", "Average monthly headcount"]},
    "PT": {"revenue": ["Volume de negócios", "Vendas e serviços prestados", "Rendimentos operacionais"],
           "profit": ["Resultado líquido do período", "Resultado líquido do exercício"],
           "assets": ["Total do ativo", "Ativo total"], "employees": ["Número médio de trabalhadores", "Efetivo médio"]},
    "BR": {"revenue": ["Receita operacional líquida", "Receita líquida de vendas e serviços", "Receita líquida"],
           "profit": ["Lucro (prejuízo) líquido do exercício", "Resultado líquido do exercício"],
           "assets": ["Ativo total", "Total do ativo"], "employees": ["Número médio de colaboradores", "Quadro médio de funcionários"]},
}
UNITS_NOTE = {
    "UK": ("All figures in £", "All figures in £'000"),
    "PT": ("Valores em euros", "Valores em milhares de euros"),
    "BR": ("Valores em reais (R$)", "Valores expressos em milhares de reais (R$ mil)"),
}
RISKS = {
    "UK": ["The principal risks facing the company are competition, customer concentration and the availability of skilled staff.",
           "Rising energy and input costs remain the most significant risk; the company has fixed prices for part of next year's supply.",
           "The directors consider the main risks to be credit risk on trade receivables and the renewal of key contracts.",
           "Cyber security is a growing risk. The company completed an external penetration test during the year."],
    "PT": ["Os principais riscos da empresa são a concorrência, a concentração de clientes e a escassez de mão de obra qualificada.",
           "O aumento dos custos de energia e matérias-primas continua a ser o risco mais relevante.",
           "O órgão de gestão considera que os principais riscos são o risco de crédito de clientes e a renovação de contratos-chave."],
    "BR": ["Os principais riscos são a concorrência, a concentração de clientes e a variação cambial sobre insumos importados.",
           "A volatilidade da taxa de juros e o custo do crédito seguem como os riscos mais relevantes.",
           "A administração destaca o risco de crédito de clientes e a dependência de poucos contratos relevantes."],
}
OUTLOOK = {
    "UK": ["Trading in the first months of the new year has been in line with the board's expectations.",
           "The board expects modest growth next year, supported by new contracts signed after the year end.",
           "Next year will focus on margin recovery and tighter working-capital management."],
    "PT": ["A atividade nos primeiros meses do novo exercício está em linha com as expectativas do órgão de gestão.",
           "Para o próximo exercício prevê-se um crescimento moderado, suportado por novos contratos.",
           "O próximo exercício será dedicado à recuperação de margens e à gestão do fundo de maneio."],
    "BR": ["Os primeiros meses do novo exercício estão em linha com as expectativas da administração.",
           "A administração espera crescimento moderado no próximo exercício, apoiado por novos contratos.",
           "O foco do próximo exercício será a recuperação de margens e a gestão do capital de giro."],
}
ESG = {
    "UK": ["The company reduced its Scope 1 and 2 emissions by {p}% during the year and installed solar panels at its main site.",
           "Staff turnover fell to {p}% and the company introduced a new wellbeing programme."],
    "PT": ["A empresa reduziu as emissões de âmbito 1 e 2 em {p}% e instalou painéis solares na unidade principal.",
           "A rotação de pessoal desceu para {p}% e foi lançado um programa de bem-estar."],
    "BR": ["A companhia reduziu em {p}% as emissões de escopo 1 e 2 e instalou painéis solares na unidade principal.",
           "A rotatividade caiu para {p}% e a empresa lançou um programa de bem-estar."],
}


def money(amount: int, country: str, thousands: bool = False, negative_parens: bool = True) -> str:
    v = amount // 1000 if thousands else amount
    neg = v < 0
    v = abs(v)
    if country == "UK":
        s = f"£{v:,}"
        return f"({s})" if neg else s
    s = f"{v:,}".replace(",", ".")
    if country == "PT":
        return f"-{s} €" if neg else f"{s} €"
    s = f"R$ {s}"
    return f"({s})" if neg and negative_parens else (f"-{s}" if neg else s)


def millions(amount: int, country: str) -> str:
    m = amount / 1_000_000
    if country == "UK":
        return f"£{m:,.1f} million" if m >= 1 else f"£{amount // 1000:,}k"
    txt = f"{m:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if country == "PT":
        return f"{txt} milhões de euros" if m >= 1 else f"{amount // 1000} mil euros"
    return f"R$ {txt} milhões" if m >= 1 else f"R$ {amount // 1000} mil"


def change_sentence(country: str, label: str, cur: int, prev: int | None, year: int) -> str:
    if prev is None or prev == 0:
        return {"UK": f"{label} for the year was {millions(cur, country)}.",
                "PT": f"O {label.lower()} do exercício ascendeu a {millions(cur, country)}.",
                "BR": f"A {label.lower()} do exercício foi de {millions(cur, country)}."}[country]
    pct = round((cur - prev) / abs(prev) * 100)
    if country == "UK":
        verb = "rose" if pct >= 0 else "fell"
        return f"{label} {verb} {abs(pct)}% to {millions(cur, country)} ({year - 1}: {millions(prev, country)})."
    if country == "PT":
        verb = "aumentou" if pct >= 0 else "diminuiu"
        return f"O {label.lower()} {verb} {abs(pct)}% para {millions(cur, country)} ({year - 1}: {millions(prev, country)})."
    verb = "cresceu" if pct >= 0 else "recuou"
    return f"A {label.lower()} {verb} {abs(pct)}%, para {millions(cur, country)} ({year - 1}: {millions(prev, country)})."


def annual_report(rng: random.Random, country: str, legal_name: str, reg_no: str, display_name: str, sector_desc: str,
                  f: dict, prior: dict | None, group_of: tuple[str, int] | None = None) -> str:
    """Markdown annual-report extract. `group_of` = (subsidiary legal name, subsidiary revenue) for group accounts."""
    L = LABELS[country]
    rev_l, prof_l = rng.choice(L["revenue"]), rng.choice(L["profit"])
    assets_l, emp_l = rng.choice(L["assets"]), rng.choice(L["employees"])
    thousands = rng.random() < 0.4
    y = f["year"]
    show_reg = rng.random() < 0.75
    reg_line = {"UK": f"Company registration number: {reg_no}", "PT": f"NIPC: {reg_no}", "BR": f"CNPJ: {reg_no}"}[country] if show_reg else ""
    group = group_of is not None
    p = rng.randint(3, 18)

    def row(label, key, fmt=True):
        cur = money(f[key], country, thousands) if fmt else f"{f[key]}"
        if prior:
            prv = money(prior[key], country, thousands) if fmt else f"{prior[key]}"
            return f"| {label} | {cur} | {prv} |"
        return f"| {label} | {cur} |"

    head = f"| | {y} | {y - 1} |\n|---|---|---|" if prior else f"| | {y} |\n|---|---|"
    table = "\n".join([head, row(rev_l, "revenue"), row(prof_l, "net_profit"), row(assets_l, "total_assets"), row(emp_l, "employees", fmt=False)])
    units = UNITS_NOTE[country][1 if thousands else 0]
    narrative = change_sentence(country, rev_l, f["revenue"], prior["revenue"] if prior else None, y)
    loss = f["net_profit"] < 0

    if country == "UK":
        title = ("Group Strategic Report and Consolidated Financial Statements" if group else "Annual Report and Financial Statements")
        gc = ("The directors have identified a material uncertainty that may cast significant doubt on the company's ability to continue as a going concern, related to the renewal of its banking facilities."
              if f["going_concern_uncertainty"] else
              "The directors have a reasonable expectation that the company has adequate resources to continue in operational existence for the foreseeable future. The financial statements are therefore prepared on the going concern basis.")
        sub = (f"\n\nThe Group's principal trading subsidiary, {group_of[0]}, contributed revenue of {millions(group_of[1], country)}. "
               "The figures below are for the Group as a whole.") if group else ""
        body = f"""# {legal_name}
## {title} for the year ended 31 December {y}

{reg_line}

## Strategic report

{display_name} {'is the holding company of a group operating' if group else 'operates'} in {sector_desc.lower()}. {narrative} {'The company made a loss for the year, reflecting lower volumes and higher input costs.' if loss else 'Profit for the year reflected tight cost control.'}{sub}

### Principal risks and uncertainties

{rng.choice(RISKS['UK'])}

### Key performance indicators

{units}.

{table}

### Environment and people

{rng.choice(ESG['UK']).format(p=p)}

## Going concern

{gc}

## Outlook

{rng.choice(OUTLOOK['UK'])}

## Independent auditor's report

We have audited the financial statements of {legal_name} for the year ended 31 December {y}. In our opinion the financial statements give a true and fair view of the state of the {'group' if group else "company"}'s affairs as at 31 December {y} and of its {'loss' if loss else 'profit'} for the year then ended.

{f['auditor']}, Statutory Auditor
"""
    elif country == "PT":
        gc = ("O órgão de gestão identificou uma incerteza material que pode suscitar dúvidas significativas sobre a capacidade da empresa de prosseguir em continuidade, relacionada com a renegociação do financiamento bancário."
              if f["going_concern_uncertainty"] else
              "As demonstrações financeiras foram preparadas no pressuposto da continuidade das operações.")
        sub = (f"\n\nA principal participada do grupo, {group_of[0]}, contribuiu com {millions(group_of[1], country)} de volume de negócios. Os valores abaixo são consolidados.") if group else ""
        body = f"""# {legal_name}
## Relatório e Contas {'Consolidadas ' if group else ''}do exercício de {y}

{reg_line}

## Relatório de gestão

A {display_name} {'é a sociedade-mãe de um grupo que atua' if group else 'desenvolve a sua atividade'} na área de {sector_desc.lower()}. {narrative} {'O exercício encerrou com prejuízo, refletindo a quebra de volumes e o aumento de custos.' if loss else 'O resultado reflete um controlo rigoroso de custos.'}{sub}

### Principais riscos e incertezas

{rng.choice(RISKS['PT'])}

### Principais indicadores

{units}.

{table}

### Ambiente e pessoas

{rng.choice(ESG['PT']).format(p=p)}

## Continuidade

{gc}

## Perspetivas

{rng.choice(OUTLOOK['PT'])}

## Certificação legal das contas

Auditámos as demonstrações financeiras {'consolidadas ' if group else ''}anexas da {legal_name}. Em nossa opinião, apresentam de forma verdadeira e apropriada a posição financeira em 31 de dezembro de {y}.

{f['auditor']}
"""
    else:
        gc = ("A administração identificou incerteza relevante relacionada à continuidade operacional, em razão da renegociação de dívidas bancárias em andamento."
              if f["going_concern_uncertainty"] else
              "As demonstrações financeiras foram elaboradas com base no pressuposto de continuidade operacional.")
        sub = (f"\n\nA principal controlada do grupo, {group_of[0]}, respondeu por receita de {millions(group_of[1], country)}. Os valores abaixo são consolidados.") if group else ""
        body = f"""# {legal_name}
## Demonstrações Financeiras {'Consolidadas ' if group else ''}do exercício de {y}

{reg_line}

## Relatório da administração

A {display_name} {'é a controladora de um grupo que atua' if group else 'atua'} no segmento de {sector_desc.lower()}. {narrative} {'O exercício terminou com prejuízo, reflexo da queda de volumes e da alta de custos.' if loss else 'O resultado reflete a disciplina de custos.'}{sub}

### Principais riscos

{rng.choice(RISKS['BR'])}

### Destaques financeiros

{units}.

{table}

### Sustentabilidade e pessoas

{rng.choice(ESG['BR']).format(p=p)}

## Continuidade operacional

{gc}

## Perspectivas

{rng.choice(OUTLOOK['BR'])}

## Relatório do auditor independente

Examinamos as demonstrações financeiras {'consolidadas ' if group else ''}da {legal_name}. Em nossa opinião, elas apresentam adequadamente, em todos os aspectos relevantes, a posição patrimonial e financeira em 31 de dezembro de {y}.

{f['auditor']}
"""
    return body.replace("\n\n\n", "\n\n")
