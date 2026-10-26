---
name: kyc-pt
description: Procedimento KYC para empresas registadas em Portugal (registo comercial, NIPC, sócios e beneficiários efetivos, notícias em português). Usar ao investigar uma empresa portuguesa para onboarding.
---

# Procedimento KYC: Portugal

Seguir os passos por ordem. Os nomes das ferramentas são as ferramentas KYC disponíveis; indicar o país `PT` quando a ferramenta o pedir.

1. Pesquisar a entidade por denominação no registo comercial (`registry_search_company`). Confirmar a correspondência com a sede e, quando declarado pelo cliente, com o NIPC.
2. Obter a certidão (`registry_get_company`): denominação, NIPC, forma jurídica, estado, data de constituição, sede e CAE.
3. Identificar os órgãos sociais (gerentes e administradores) com `registry_get_officers_and_owners`.
4. Identificar os sócios e respetivas participações com a mesma ferramenta. Para sócios que sejam pessoas coletivas, consultar a entidade pelo NIPC e continuar até aos beneficiários efetivos (pessoas singulares com 25% ou mais, direta ou indiretamente).
5. O estado deve ser `ativa`. `dissolvida` ou `insolvente` significa que a entidade não está ativa.
6. Pesquisar notícias em português pela denominação da empresa (`news_search` e `news_search_web`).
7. Usar o relatório e contas mais recente (`summary_get_financial_statements`): volume de negócios, resultado líquido, nota sobre continuidade.
