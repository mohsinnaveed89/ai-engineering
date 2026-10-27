# UK-020: Blackwater Harbour Media (UK)

Score: 74% | difficulty: hard | cases: conflicting_data, negative_news

| field | score | expected | actual |
|---|---|---|---|
| legal_name | 100% | Blackwater Harbour Media PLC | BLACKWATER HARBOUR MEDIA PLC |
| registration_number | 100% | 03176519 | 03176519 |
| status | 100% | active | active |
| address | 100% | 167 Bridge Street, Bristol, BS1 8BN, United Kingdom | 167 Bridge Street, Bristol, BS1 8BN, England |
| directors | 100% | ["Emily Smith", "Fiona Harrison", "Olivia Wood"] | ["Emily Smith", "Fiona Harrison", "Olivia Wood"] |
| beneficial_owners | 100% | ["Emily Smith", "Fiona Harrison"] | ["Emily Smith", "Fiona Harrison"] |
| adverse_news | 0% | ["https://nbpost.example/2025/03/supplier-sues-blackwater-harbour-media-over-unpaid-invoices-9697"] | [] |
| financials | 100% | {"year": 2025, "currency": "GBP", "revenue": 5834000, "net_profit": 286000} | {"year": 2025, "currency": "GBP", "revenue": 5834000.0, "net_profit": 286000.0} |
| risk_rating | 0% | medium | low |
| recommendation | 0% | investigate_further | approve |
| flags | 67% | ["adverse_media", "conflicting_data"] | ["conflicting_data", "other"] |

Schema: 100% | Sourcing: 100%

Flag messages:
- [medium] conflicting_data: address: register "167 Bridge Street, Bristol, BS1 8BN" [bank-reg] vs bank prospect record "28 Station Road, Belfast, BT1 9ZA" [bank-prospect]. The register prevails for identity; a human should confirm which is correct.
- [low] other: Fiona Harrison is recorded in the 25-50% PSC band. Her UBO status (25% or more) relies on the lower bound of that band (25%). The register does not give an exact percentage. Emily Smith is recorded in the 50-75% band; the lower bound (50%) also meets the threshold.
- [low] other: The registry_trace_ultimate_owners tool was not available to the ownership check, so it was not run. Both registered PSCs are natural persons with no corporate shareholder, so no look-through was needed.
- [low] other: Adverse media: none found in archive or live search. news-0036 is a neutral supplier-of-the-year article that matches on company name only and is not confirmed as about this company's Bristol/media profile. It was not scored.
- [low] other: Owner jurisdictions are not recorded in the register for either owner (Emily Smith 60%, Fiona Harrison 40%), so the high-risk jurisdiction test could not be completed for the owners. No high-risk jurisdiction was raised.
- [low] other: Bank relationship check: not an existing customer. No customer_since, products, internal risk rating or last KYC review is held in the bank system for this registration number.
