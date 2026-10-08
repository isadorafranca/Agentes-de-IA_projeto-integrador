# Agentes de IA para Análise Inteligente da Superstore USA

Sistema multiagente para análise do dataset Sample Superstore e geração de relatórios personalizados para CEO, Vendas e Produtos/Logística.

## O que foi implementado

Este repositório fecha as lacunas identificadas no notebook original:

- Workflow explícito com `SuperstoreWorkflow` e estado compartilhado em `session_state`;
- cache do dossiê analítico para evitar recomputação;
- tools registradas com `@tool` quando o Agno está disponível;
- métricas de crescimento mensal, tendências e detecção de anomalias;
- filtros por período, região e vendedor quando a coluna existe;
- validação de colunas, tipos, datas, vendas negativas e divisão por zero;
- documentação formal da arquitetura;
- estrutura pronta para GitHub, Colab e testes automatizados.

## Estrutura

```text
.
├── data/                         # coloque aqui o CSV; não versionar dados sensíveis
├── docs/architecture.md         # arquitetura e fluxo
├── notebooks/                   # notebook executável no Google Colab
├── outputs/                     # relatórios gerados; ignorados pelo Git
├── src/superstore_ai/pipeline.py
├── tests/test_pipeline.py
├── requirements.txt
└── .gitignore
```

## Instalação local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
```

Ou instale o pacote em modo editável: `pip install -e .[test]`.

## Execução no Colab

1. Faça upload de `SampleSuperstore.csv` e do notebook `notebooks/Projeto_integrador_Isadora_Franca_atualizado.ipynb`.
2. Instale as dependências na primeira célula.
3. Configure `GEMINI_API_KEY` ou `GOOGLE_API_KEY` nos Secrets do Colab. Nunca coloque a chave diretamente no notebook.
4. Execute as células na ordem.
5. Para uma execução sem consumo de API, use `workflow.run(use_llm=False)`; isso valida o pipeline e gera relatórios dry-run.
6. Para relatórios gerados pelo Gemini, use `workflow.run(use_llm=True)`.

## Execução local

```bash
export PYTHONPATH=src
python -m pytest -q
python - <<'PY'
from superstore_ai.pipeline import SuperstoreWorkflow, find_dataset, load_and_prepare_data

df = load_and_prepare_data(find_dataset('.'))
workflow = SuperstoreWorkflow(df, output_dir='outputs')
workflow.run(use_llm=False)
print(workflow.session_state.metadata)
PY
```

## Segurança e publicação

- Use Secrets do Colab ou variáveis de ambiente para a API.
- Não versione CSVs, relatórios de execução, `.env`, tokens ou chaves.
- Antes de publicar, revise o histórico do Git para garantir que nenhuma credencial foi commitada.

## Limitações conhecidas

A API do Agno pode alterar caminhos de importação entre versões. O módulo possui imports compatíveis e fallback para testes sem Agno. Em ambiente Colab, valide a versão instalada e ajuste apenas o import do Workflow se a versão escolhida exigir outra API nativa.
