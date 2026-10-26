---
name: kyc-br
description: Procedimento KYC para empresas registradas no Brasil (cadastro de CNPJ, QSA, beneficiários finais, notícias em português). Usar ao investigar uma empresa brasileira para onboarding.
---

# Procedimento KYC: Brasil

Seguir os passos em ordem. Os nomes das ferramentas são as ferramentas KYC disponíveis; informar o país `BR` quando a ferramenta o pedir.

1. O cadastro de CNPJ só permite consulta pelo número do CNPJ; não há pesquisa por nome. Obtenha o CNPJ declarado na ficha de proposta (sistema interno do banco, `br_find_prospect_cnpj`) ou em outras fontes confiáveis, e confirme-o no cadastro (`registry_get_company`).
2. Obter os dados cadastrais: razão social, nome fantasia, situação cadastral, data de início de atividade, natureza jurídica, CNAE e endereço.
3. Identificar o quadro de sócios e administradores (QSA) com `registry_get_officers_and_owners`. Administradores têm qualificação "Administrador" ou "Sócio-Administrador".
4. Para sócios pessoa jurídica, consultar o CNPJ do sócio e continuar até os beneficiários finais (pessoas físicas com 25% ou mais, direta ou indiretamente). Sócios domiciliados no exterior em jurisdições de alto risco devem ser sinalizados.
5. A situação cadastral deve ser `ATIVA`. `BAIXADA`, `INAPTA` ou `SUSPENSA` significam que a empresa não está ativa.
6. Pesquisar notícias em português pela razão social e pelo nome fantasia (`news_search` e `news_search_web`).
7. Usar as demonstrações financeiras mais recentes (`summary_get_financial_statements`): receita operacional líquida, lucro líquido, continuidade operacional.
