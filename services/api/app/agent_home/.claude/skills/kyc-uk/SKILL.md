---
name: kyc-uk
description: KYC procedure for companies registered in the United Kingdom (Companies House register, PSC bands, English-language news). Use when researching a UK company for onboarding.
---

# KYC procedure: United Kingdom

Follow these steps in order. Tool names are the KYC tools you have; pass country `UK` where a tool takes one.

1. Search the company register by name (`registry_search_company`). Confirm the match using the registered office address and, where the prospect declared it, the company number.
2. Retrieve the company profile (`registry_get_company`): name, number, status, date of creation, registered office, SIC codes.
3. Retrieve officers (`registry_get_officers_and_owners`). Directors are officers with the role `director`; secretaries are not directors.
4. Retrieve persons with significant control (PSC) from the same tool. Ownership is reported in bands (25 to 50%, 50 to 75%, 75 to 100%). For corporate PSCs, look up the corporate entity by its registration number and continue up the chain.
5. Status must be `active`. `dissolved` or `liquidation` means the company is not active.
6. Search English-language news for the company name and its trading name (`news_search` and `news_search_web`).
7. Use the most recent annual report (`summary_get_financial_statements`): turnover, profit for the year, going concern statement.
