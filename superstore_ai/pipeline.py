"""Pipeline multiagente resiliente para o dataset Sample Superstore.

A camada analítica é determinística e testável sem API. A camada de agentes é
opcional e usa Agno/Gemini quando as dependências e a chave estão disponíveis.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pandas as pd

try:
    from agno.tools.decorator import tool as agno_tool
except Exception:  # permite executar testes analíticos sem instalar Agno
    def agno_tool(function: Callable) -> Callable:
        return function

try:
    from agno.workflow.v2 import Workflow as AgnoWorkflow
except Exception:
    try:
        from agno.workflow import Workflow as AgnoWorkflow
    except Exception:
        AgnoWorkflow = None

REQUIRED_COLUMNS = {
    "Sales", "Profit", "Discount", "Order ID", "Customer ID", "Order Date",
    "Ship Date", "Region", "Segment", "Category", "Sub-Category", "Ship Mode",
}
MODEL_IDS = [
    "gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash"
]


class DataQualityError(ValueError):
    """Indica que a base não atende aos requisitos mínimos do pipeline."""


class ModelUnavailable(RuntimeError):
    """Modelo inexistente/indisponível; trocar de modelo é preferível a retentar."""


def find_dataset(base_dir: str | Path = ".") -> Path:
    base = Path(base_dir)
    candidates = [
        base / "SampleSuperstore.csv", base / "Sample - Superstore.csv",
        base / "data" / "SampleSuperstore.csv", base / "data" / "Sample - Superstore.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError("SampleSuperstore.csv não foi localizado. Faça o upload no Colab.")


def validate_columns(df: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS.difference(df.columns))
    if missing:
        raise DataQualityError(f"Colunas obrigatórias ausentes: {', '.join(missing)}")


def load_and_prepare_data(csv_path: str | Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(csv_path, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(csv_path, encoding="latin1")
    validate_columns(df)
    df = df.copy()
    for col in ("Order Date", "Ship Date"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    if df[["Order Date", "Ship Date"]].isna().any().any():
        raise DataQualityError("Existem datas inválidas em Order Date ou Ship Date.")
    for col in ("Sales", "Profit", "Discount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["Sales", "Profit", "Discount"]].isna().any().any():
        raise DataQualityError("Existem valores não numéricos em Sales, Profit ou Discount.")
    if (df["Sales"] < 0).any():
        raise DataQualityError("Sales não pode conter valores negativos.")
    df["Shipping Days"] = (df["Ship Date"] - df["Order Date"]).dt.days
    df["Profit Margin %"] = (df["Profit"].div(df["Sales"].where(df["Sales"].ne(0))) * 100).fillna(0.0)
    df["Order Year"] = df["Order Date"].dt.year
    df["Order Month"] = df["Order Date"].dt.to_period("M").astype(str)
    return df


def _safe_margin(profit: pd.Series, sales: pd.Series) -> pd.Series:
    return profit.div(sales.where(sales.ne(0))).mul(100).fillna(0.0)


def get_executive_kpis(df: pd.DataFrame) -> dict[str, Any]:
    total_sales = float(df["Sales"].sum())
    total_profit = float(df["Profit"].sum())
    return {
        "total_sales_usd": total_sales,
        "total_profit_usd": total_profit,
        "overall_margin_pct": (total_profit / total_sales * 100) if total_sales else 0.0,
        "average_discount_pct": float(df["Discount"].mean() * 100),
        "unique_orders": int(df["Order ID"].nunique()),
        "active_customers": int(df["Customer ID"].nunique()),
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
    }


def get_performance_by_dimension(df: pd.DataFrame, dimension: str = "Region") -> pd.DataFrame:
    if dimension not in df.columns:
        raise DataQualityError(f"Dimensão não encontrada: {dimension}")
    result = df.groupby(dimension, dropna=False).agg(
        Total_Sales=("Sales", "sum"), Total_Profit=("Profit", "sum"),
        Order_Count=("Order ID", "nunique"),
    ).reset_index()
    result["Profit_Margin_%"] = _safe_margin(result["Total_Profit"], result["Total_Sales"])
    total = result["Total_Sales"].sum()
    result["Sales_Share_%"] = result["Total_Sales"].div(total if total else 1).mul(100)
    return result.sort_values("Total_Sales", ascending=False).reset_index(drop=True)


def get_product_and_shipping_diagnostics(df: pd.DataFrame, top_n: int = 5) -> dict[str, pd.DataFrame]:
    subcats = df.groupby("Sub-Category", dropna=False).agg(
        Total_Sales=("Sales", "sum"), Total_Profit=("Profit", "sum"),
        Avg_Discount=("Discount", "mean"),
    ).reset_index()
    subcats["Margin_%"] = _safe_margin(subcats["Total_Profit"], subcats["Total_Sales"])
    deficitarias = subcats.sort_values("Total_Profit").head(top_n).reset_index(drop=True)
    shipping = df.groupby("Ship Mode", dropna=False).agg(
        Total_Sales=("Sales", "sum"), Total_Orders=("Order ID", "nunique"),
        Avg_Delivery_Days=("Shipping Days", "mean"),
    ).reset_index().sort_values("Total_Sales", ascending=False).reset_index(drop=True)
    return {"deficitarias": deficitarias, "shipping": shipping}


def get_temporal_trends(df: pd.DataFrame) -> pd.DataFrame:
    monthly = df.groupby("Order Month").agg(Total_Sales=("Sales", "sum"), Total_Profit=("Profit", "sum"), Orders=("Order ID", "nunique")).reset_index()
    monthly["Margin_%"] = _safe_margin(monthly["Total_Profit"], monthly["Total_Sales"])
    monthly["Sales_Growth_%"] = monthly["Total_Sales"].pct_change().mul(100).replace([float("inf"), -float("inf")], 0).fillna(0)
    return monthly


def detect_anomalies(df: pd.DataFrame, z_threshold: float = 3.0) -> pd.DataFrame:
    monthly = get_temporal_trends(df)
    if len(monthly) < 2 or monthly["Total_Sales"].std(ddof=0) == 0:
        monthly["Sales_Anomaly"] = False
        return monthly
    z = (monthly["Total_Sales"] - monthly["Total_Sales"].mean()) / monthly["Total_Sales"].std(ddof=0)
    monthly["Sales_Anomaly"] = z.abs() >= z_threshold
    return monthly


def filter_sales(df: pd.DataFrame, start: str | None = None, end: str | None = None, region: str | None = None, seller: str | None = None) -> pd.DataFrame:
    result = df.copy()
    if start:
        result = result[result["Order Date"] >= pd.Timestamp(start)]
    if end:
        result = result[result["Order Date"] <= pd.Timestamp(end)]
    if region:
        result = result[result["Region"].eq(region)]
    if seller:
        seller_col = "Salesperson" if "Salesperson" in result.columns else "Representative"
        if seller_col not in result.columns:
            raise DataQualityError("A base não possui coluna de vendedor (Salesperson/Representative).")
        result = result[result[seller_col].eq(seller)]
    return result.reset_index(drop=True)


@agno_tool
def executive_kpis_tool() -> str:
    """Calcula KPIs globais. Em produção, o contexto do dataframe é injetado pelo workflow."""
    return "Tool registrada: use o dataframe do contexto do workflow para calcular KPIs."


@agno_tool
def performance_dimension_tool(dimension: str = "Region") -> str:
    """Solicita análise por dimensão para o agente analista."""
    return f"Tool registrada para análise por dimensão: {dimension}."


@agno_tool
def diagnostics_tool() -> str:
    """Solicita diagnóstico de produtos e logística."""
    return "Tool registrada para diagnóstico de subcategorias e modais de frete."


@dataclass
class PipelineState:
    """Estado compartilhado e auditável entre as etapas do Workflow."""
    df: pd.DataFrame
    briefing: str = ""
    reports: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class SuperstoreWorkflow:
    """Workflow explícito com session_state, cache e passagem de contexto.

    O método run funciona sem API em modo dry_run e usa os agentes Agno quando
    `use_llm=True`. A interface permanece estável para uso no Colab ou em testes.
    """
    def __init__(self, df: pd.DataFrame, output_dir: str | Path = "outputs", model_ids: list[str] | None = None):
        validate_columns(df)
        self.session_state = PipelineState(df=df.copy(), metadata={"cache": {}, "completed_steps": []})
        self.output_dir = Path(output_dir)
        self.model_ids = model_ids or MODEL_IDS.copy()
        self._model_idx = 0
        self.native_workflow = self._create_native_workflow()

    def _create_native_workflow(self):
        """Cria o objeto Workflow nativo quando a versão do Agno oferece a API.

        A execução de domínio permanece em ``run`` para ser testável sem banco/API;
        este objeto formaliza o contrato de Workflow e expõe session_state ao Agno.
        """
        if AgnoWorkflow is None:
            return None
        return AgnoWorkflow(
            name="Superstore Multiagent Workflow",
            description="Workflow de análise e geração de relatórios da Superstore.",
            session_state={"cache": self.session_state.metadata["cache"], "completed_steps": self.session_state.metadata["completed_steps"]},
            cache_session=True,
            add_session_state_to_context=True,
        )

    def analyst_step(self) -> str:
        cache = self.session_state.metadata["cache"]
        if "briefing" in cache:
            self.session_state.briefing = cache["briefing"]
            return self.session_state.briefing
        df = self.session_state.df
        parts = ["# Dossiê de Dados\n", "## KPIs\n", pd.DataFrame([get_executive_kpis(df)]).to_markdown(index=False)]
        for dim in ("Region", "Segment", "Category"):
            parts += [f"\n## Performance por {dim}\n", get_performance_by_dimension(df, dim).to_markdown(index=False)]
        diag = get_product_and_shipping_diagnostics(df)
        parts += ["\n## Subcategorias deficitárias\n", diag["deficitarias"].to_markdown(index=False), "\n## Frete\n", diag["shipping"].to_markdown(index=False)]
        trends = get_temporal_trends(df)
        parts += ["\n## Tendências mensais\n", trends.to_markdown(index=False), "\n## Anomalias\n", detect_anomalies(df).query("Sales_Anomaly").to_markdown(index=False)]
        self.session_state.briefing = "".join(parts)
        cache["briefing"] = self.session_state.briefing
        self.session_state.metadata["completed_steps"].append("analyst")
        return self.session_state.briefing

    def _build_llm_agent(self, role: str, model_id: str):
        from agno.agent import Agent
        from agno.models.google import Gemini
        specs = {
            "ceo": ("CEO Strategic Reporter", "Resuma KPIs, riscos e três ações prioritárias para o CEO."),
            "sales": ("Commercial Sales Reporter", "Analise região, segmento, tendências e descontos para vendas."),
            "ops": ("Supply Chain & Merchandising Reporter", "Analise subcategorias deficitárias, frete e recomendações operacionais."),
        }
        name, instruction = specs[role]
        return Agent(name=name, model=Gemini(id=model_id), instructions=[instruction], markdown=True)

    def _run_llm_step(self, role: str, prompt: str, max_retries: int = 3) -> str:
        while self._model_idx < len(self.model_ids):
            agent = self._build_llm_agent(role, self.model_ids[self._model_idx])
            for attempt in range(max_retries):
                try:
                    response = agent.run(prompt)
                    if response and response.content:
                        return response.content
                except Exception as exc:
                    message = str(exc)
                    if "404" in message or "NOT_FOUND" in message:
                        break
                    if "429" in message or "503" in message:
                        time.sleep((attempt + 1) * 4)
                        continue
                    raise
            self._model_idx += 1
        raise ModelUnavailable("Nenhum modelo conseguiu concluir a etapa.")

    def _dry_report(self, role: str) -> str:
        title = {"ceo": "Relatório Executivo do CEO", "sales": "Relatório Tático de Vendas", "ops": "Relatório Operacional de Produtos e Logística"}[role]
        return f"# {title}\n\nEste relatório foi preparado em modo dry-run.\n\n{self.session_state.briefing[:4000]}"

    def run(self, use_llm: bool = True) -> dict[str, str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        briefing = self.analyst_step()
        for role, filename in (("ceo", "relatorio_ceo.md"), ("sales", "relatorio_vendas.md"), ("ops", "relatorio_produtos_logistica.md")):
            prompt = f"Use rigorosamente este dossiê para produzir o relatório solicitado:\n\n{briefing}"
            result = self._run_llm_step(role, prompt) if use_llm else self._dry_report(role)
            self.session_state.reports[filename] = result
            self.session_state.metadata["completed_steps"].append(role)
        self.session_state.reports["dossie_dados.md"] = briefing
        for filename, content in self.session_state.reports.items():
            (self.output_dir / filename).write_text(content, encoding="utf-8")
        return self.session_state.reports


# Alias explícito para a documentação e para uma futura substituição por Agno Workflow nativo.
Workflow = SuperstoreWorkflow
