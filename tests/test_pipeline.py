import pandas as pd
import pytest

from superstore_ai.pipeline import (
    DataQualityError,
    SuperstoreWorkflow,
    detect_anomalies,
    filter_sales,
    get_executive_kpis,
    get_performance_by_dimension,
    get_temporal_trends,
    load_and_prepare_data,
    validate_columns,
)


def sample_df():
    return pd.DataFrame({
        "Order ID": ["1", "2", "3", "4"], "Customer ID": ["c1", "c2", "c1", "c3"],
        "Order Date": ["2024-01-01", "2024-01-15", "2024-02-01", "2024-02-15"],
        "Ship Date": ["2024-01-03", "2024-01-18", "2024-02-04", "2024-02-18"],
        "Sales": [100.0, 0.0, 300.0, 600.0], "Profit": [20.0, 0.0, -30.0, 120.0],
        "Discount": [0.1, 0.2, 0.0, 0.3], "Region": ["East", "East", "West", "West"],
        "Segment": ["Consumer"] * 4, "Category": ["Office"] * 4,
        "Sub-Category": ["Paper", "Paper", "Chairs", "Chairs"], "Ship Mode": ["First"] * 4,
    })


def test_prepare_data_adds_features_and_handles_zero_sales(tmp_path):
    path = tmp_path / "sample.csv"
    sample_df().to_csv(path, index=False)
    df = load_and_prepare_data(path)
    assert "Shipping Days" in df
    assert "Order Month" in df
    assert df.loc[df["Sales"].eq(0), "Profit Margin %"].iloc[0] == 0


def test_validate_columns_reports_missing_columns():
    with pytest.raises(DataQualityError, match="Colunas obrigatórias ausentes"):
        validate_columns(pd.DataFrame({"Sales": [1]}))


def test_kpis_and_dimension_metrics():
    df = load_and_prepare_data_from_frame()
    kpis = get_executive_kpis(df)
    assert kpis["total_sales_usd"] == 1000
    assert kpis["unique_orders"] == 4
    result = get_performance_by_dimension(df, "Region")
    assert list(result["Region"]) == ["West", "East"]
    assert "Profit_Margin_%" in result


def test_temporal_trends_growth_and_anomalies():
    df = load_and_prepare_data_from_frame()
    trends = get_temporal_trends(df)
    assert len(trends) == 2
    assert "Sales_Growth_%" in trends
    anomalies = detect_anomalies(df)
    assert "Sales_Anomaly" in anomalies


def test_filters_by_period_and_region():
    df = load_and_prepare_data_from_frame()
    result = filter_sales(df, start="2024-02-01", region="West")
    assert len(result) == 2


def test_workflow_uses_cache_and_generates_outputs(tmp_path):
    workflow = SuperstoreWorkflow(load_and_prepare_data_from_frame(), output_dir=tmp_path)
    first = workflow.analyst_step()
    second = workflow.analyst_step()
    assert first == second
    assert workflow.session_state.metadata["completed_steps"].count("analyst") == 1
    reports = workflow.run(use_llm=False)
    assert len(reports) == 4
    assert (tmp_path / "dossie_dados.md").exists()


def load_and_prepare_data_from_frame():
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(suffix=".csv") as handle:
        sample_df().to_csv(handle.name, index=False)
        return load_and_prepare_data(handle.name)
