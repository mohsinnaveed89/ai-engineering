# Business Customer KYC Policy (sample)

Version 2026.2 · Applies to: onboarding of business (non-personal) customers in the United Kingdom, Portugal and Brazil.

> This is a short, simplified sample policy written for the AI Academy. It is not legal or compliance advice.

## 1. Purpose

Before the bank onboards a business customer it must establish who the customer is, who owns and controls it, and whether it is safe to work with. The analyst prepares a KYC onboarding pack; a human approver makes the final decision.

## 2. Principles

1. **No source, no claim.** Every fact in the pack must cite where it came from (register record, news article, internal system, document).
2. **Flag, don't judge.** Anything concerning is flagged for the approver with the evidence. Flags are not verdicts.
3. **The analyst recommends, the approver decides.** The pack contains a recommendation: `approve`, `decline` or `investigate_further`.
4. **Data minimisation.** Personal data is used only for KYC purposes and must not be copied into logs, traces or tools that do not need it.

## 3. Customer identification

Confirm from the official register of the country of incorporation:

- legal name
- registration number (UK: company number; Portugal: NIPC; Brazil: CNPJ)
- status (the company must be active)
- registered address

If the details declared by the prospect differ from the register, the register prevails and the difference is raised as a **conflicting data** flag. If a required item is missing from every available source, raise a **missing data** flag.

## 4. Ownership and control

- Identify all directors (or equivalent officers: *gerentes*, *administradores*, *sócios-administradores*). Company secretaries are not directors.
- Identify direct shareholders and look through corporate shareholders to the **ultimate beneficial owners (UBOs)**: natural persons who own, directly or indirectly, **25% or more** of the company.
- Indirect ownership is calculated by multiplying shareholdings along the chain (for example, 80% of a holding that owns 100% = 80%).
- Any corporate shareholder in the chain is a **complex ownership** flag.
- Any shareholder registered in a **high-risk jurisdiction** (see the bank's risk appetite) is a **high-risk jurisdiction** flag.
- If 25% or more of the ownership cannot be traced to natural persons, raise a **UBO unidentified** flag.

## 5. Adverse media

Search news in the local language. Only articles about the customer itself count; articles about other companies with similar names must be excluded.

- **High severity:** fraud, money laundering, bribery, sanctions, terrorist financing.
- **Medium severity:** litigation, regulatory fines, labour disputes, environmental incidents.

Any relevant adverse article is an **adverse media** flag.

## 6. Sector

The bank's risk appetite lists **prohibited sectors** (for example gambling) and **high-risk sectors** (for example money services and digital assets). Use the sector code from the register (UK SIC, PT CAE, BR CNAE).

## 7. Financial summary

Summarise the latest available annual accounts: year, currency, revenue (turnover / *volume de negócios* / *receita operacional líquida*), net profit, and any going concern uncertainty. Use the most recent year available. If no accounts are available, raise a **missing data** flag.

## 8. Risk rating

Add up risk points:

| Factor | Points |
|---|---|
| Adverse media, high severity (any) | 3 |
| Adverse media, medium severity only | 1 |
| High-risk sector | 2 |
| Complex ownership (any corporate shareholder) | 1 |
| High-risk jurisdiction in the ownership chain | 2 |
| UBO unidentified | 2 |
| Conflicting data | 1 |
| Missing data (address or annual accounts) | 1 |

Rating: **0 to 1 = low**, **2 to 3 = medium**, **4 or more = high**.

## 9. Recommendation

- **decline** if the company is not active, operates in a prohibited sector, or has high-severity adverse media relating to **sanctions**;
- otherwise **approve** if the rating is low;
- otherwise **investigate_further**.

The approver may overrule the recommendation, recording the reason.

## 10. Flag types

Use these flag types in the pack: `conflicting_data`, `missing_data`, `complex_ownership`, `high_risk_jurisdiction`, `ubo_unidentified`, `adverse_media`, `high_risk_sector`, `prohibited_sector`, `inactive_company`.
