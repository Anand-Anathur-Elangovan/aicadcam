import base64
import io
import logging
from typing import Any

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, safe for server environments
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def compute_statistics(rows: list[dict], columns: list[str] | None = None) -> dict[str, Any]:
    """Compute descriptive statistics (mean, median, std, min, max, nulls) for given data.

    Args:
        rows: List of row dicts (from BigQuery query results).
        columns: Optional list of column names to focus on. If None, all numeric columns.

    Returns:
        dict with per-column statistics and null counts.
    """
    try:
        df = _rows_to_df(rows)
        if columns:
            df = df[[c for c in columns if c in df.columns]]
        numeric = df.select_dtypes(include="number")
        stats = {}
        for col in numeric.columns:
            s = numeric[col].dropna()
            stats[col] = {
                "count": int(s.count()),
                "null_count": int(numeric[col].isna().sum()),
                "mean": round(float(s.mean()), 4),
                "median": round(float(s.median()), 4),
                "std": round(float(s.std()), 4),
                "min": round(float(s.min()), 4),
                "max": round(float(s.max()), 4),
                "p25": round(float(s.quantile(0.25)), 4),
                "p75": round(float(s.quantile(0.75)), 4),
            }
        # Value counts for string columns
        cat_stats = {}
        for col in df.select_dtypes(include="object").columns:
            vc = df[col].value_counts().head(10).to_dict()
            cat_stats[col] = {"top_values": {str(k): int(v) for k, v in vc.items()}}
        return {"numeric_stats": stats, "categorical_stats": cat_stats, "total_rows": len(df), "error": None}
    except Exception as e:
        logger.error("compute_statistics failed: %s", e)
        return {"numeric_stats": {}, "categorical_stats": {}, "total_rows": 0, "error": str(e)}


def create_chart(
    rows: list[dict],
    chart_type: str,
    x_column: str,
    y_column: str | None = None,
    title: str = "Chart",
    color_column: str | None = None,
    top_n: int = 20,
) -> dict[str, Any]:
    """Create a chart from data rows and return it as a base64-encoded PNG.

    Args:
        rows: List of row dicts.
        chart_type: One of: 'bar', 'line', 'pie', 'scatter', 'histogram', 'heatmap'.
        x_column: Column name for the X axis (or category for bar/pie).
        y_column: Column name for the Y axis (required for bar, line, scatter).
        title: Chart title.
        color_column: Optional column to use for color grouping.
        top_n: For bar/pie charts, show only top N categories.

    Returns:
        dict with base64_png (str), chart_type, title, error (str or None).
    """
    try:
        df = _rows_to_df(rows)
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor("#f8f9fa")
        ax.set_facecolor("#ffffff")

        if chart_type == "bar":
            data = df[[x_column, y_column]].dropna().groupby(x_column)[y_column].sum().nlargest(top_n)
            data.plot(kind="bar", ax=ax, color="#1a73e8")
            ax.set_xlabel(x_column)
            ax.set_ylabel(y_column)
            plt.xticks(rotation=45, ha="right")

        elif chart_type == "line":
            df_sorted = df[[x_column, y_column]].dropna().sort_values(x_column)
            ax.plot(df_sorted[x_column], df_sorted[y_column], marker="o", color="#1a73e8", linewidth=2)
            ax.set_xlabel(x_column)
            ax.set_ylabel(y_column)
            plt.xticks(rotation=45, ha="right")

        elif chart_type == "pie":
            data = df[[x_column, y_column]].dropna().groupby(x_column)[y_column].sum().nlargest(top_n)
            ax.pie(data.values, labels=data.index, autopct="%1.1f%%", startangle=90)
            ax.axis("equal")

        elif chart_type == "scatter":
            ax.scatter(df[x_column], df[y_column], alpha=0.6, color="#1a73e8")
            ax.set_xlabel(x_column)
            ax.set_ylabel(y_column)

        elif chart_type == "histogram":
            df[x_column].dropna().plot(kind="hist", ax=ax, bins=30, color="#1a73e8", edgecolor="white")
            ax.set_xlabel(x_column)
            ax.set_ylabel("Frequency")

        elif chart_type == "heatmap":
            import seaborn as sns
            numeric_df = df.select_dtypes(include="number")
            corr = numeric_df.corr()
            sns.heatmap(corr, annot=True, fmt=".2f", cmap="Blues", ax=ax)
        else:
            return {"base64_png": None, "error": f"Unknown chart_type '{chart_type}'"}

        ax.set_title(title, fontsize=14, fontweight="bold", pad=12)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)

        return {"base64_png": b64, "chart_type": chart_type, "title": title, "error": None}
    except Exception as e:
        logger.error("create_chart failed: %s", e)
        plt.close("all")
        return {"base64_png": None, "chart_type": chart_type, "title": title, "error": str(e)}


def compute_trend(
    rows: list[dict],
    date_column: str,
    value_column: str,
    period: str = "week",
) -> dict[str, Any]:
    """Compute period-over-period trend and % change for a time series.

    Args:
        rows: List of row dicts.
        date_column: Column containing dates or timestamps.
        value_column: Numeric column to trend.
        period: Aggregation period — 'day', 'week', or 'month'.

    Returns:
        dict with trend_data (list of {period, value, pct_change}),
        overall_trend ('up'/'down'/'flat'), error (str or None).
    """
    try:
        df = _rows_to_df(rows)
        df[date_column] = pd.to_datetime(df[date_column])
        df = df[[date_column, value_column]].dropna()

        freq_map = {"day": "D", "week": "W", "month": "ME"}
        freq = freq_map.get(period, "W")
        df = df.set_index(date_column).resample(freq)[value_column].sum().reset_index()
        df["pct_change"] = df[value_column].pct_change() * 100

        trend_data = []
        for _, row in df.iterrows():
            trend_data.append({
                "period": str(row[date_column].date()),
                "value": round(float(row[value_column]), 2),
                "pct_change": round(float(row["pct_change"]), 2) if not np.isnan(row["pct_change"]) else None,
            })

        if len(df) >= 2:
            first, last = df[value_column].iloc[0], df[value_column].iloc[-1]
            overall = "up" if last > first * 1.02 else "down" if last < first * 0.98 else "flat"
        else:
            overall = "insufficient_data"

        return {"trend_data": trend_data, "overall_trend": overall, "period": period, "error": None}
    except Exception as e:
        logger.error("compute_trend failed: %s", e)
        return {"trend_data": [], "overall_trend": "unknown", "error": str(e)}


def detect_anomalies(
    rows: list[dict],
    value_column: str,
    z_threshold: float = 2.5,
) -> dict[str, Any]:
    """Detect anomalous rows using Z-score method.

    Args:
        rows: List of row dicts.
        value_column: Numeric column to check for anomalies.
        z_threshold: Z-score threshold — values above this are flagged (default 2.5).

    Returns:
        dict with anomalies (list of row dicts), anomaly_count (int), error (str or None).
    """
    try:
        df = _rows_to_df(rows)
        col = df[value_column].dropna()
        mean, std = col.mean(), col.std()
        if std == 0:
            return {"anomalies": [], "anomaly_count": 0, "error": None}
        df["_zscore"] = ((df[value_column] - mean) / std).abs()
        anomalies = df[df["_zscore"] > z_threshold].drop(columns=["_zscore"])
        return {
            "anomalies": anomalies.to_dict(orient="records"),
            "anomaly_count": len(anomalies),
            "mean": round(float(mean), 4),
            "std": round(float(std), 4),
            "threshold": z_threshold,
            "error": None,
        }
    except Exception as e:
        logger.error("detect_anomalies failed: %s", e)
        return {"anomalies": [], "anomaly_count": 0, "error": str(e)}


def compute_correlation(
    rows: list[dict],
    columns: list[str] | None = None,
    method: str = "pearson",
) -> dict[str, Any]:
    """Compute pairwise correlation between numeric columns.

    Args:
        rows: List of row dicts.
        columns: Specific columns to correlate. If None, uses all numeric columns.
        method: 'pearson' or 'spearman'.

    Returns:
        dict with correlation_matrix (dict of dicts), strongest_pairs (list), error.
    """
    try:
        df = _rows_to_df(rows)
        if columns:
            df = df[[c for c in columns if c in df.columns]]
        numeric = df.select_dtypes(include="number")
        corr = numeric.corr(method=method)
        corr_dict = {col: {k: round(float(v), 4) for k, v in row.items()} for col, row in corr.to_dict().items()}

        # Find strongest pairs (excluding self-correlation)
        pairs = []
        cols = corr.columns.tolist()
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                pairs.append({"col_a": cols[i], "col_b": cols[j], "correlation": round(float(corr.iloc[i, j]), 4)})
        pairs.sort(key=lambda x: abs(x["correlation"]), reverse=True)

        return {"correlation_matrix": corr_dict, "strongest_pairs": pairs[:10], "method": method, "error": None}
    except Exception as e:
        logger.error("compute_correlation failed: %s", e)
        return {"correlation_matrix": {}, "strongest_pairs": [], "error": str(e)}


def run_python_analysis(
    rows: list[dict],
    analysis_description: str,
) -> dict[str, Any]:
    """Run a free-form pandas analysis described in plain English.

    The analysis is performed by the analytics agent itself — this tool provides
    a safe way to perform structured operations on the data.

    Args:
        rows: List of row dicts (the dataset to analyze).
        analysis_description: Plain English description of what to compute.
            Examples: 'top 5 users by message count', 'monthly active users trend',
            'departments with more than 100 employees'.

    Returns:
        dict with summary (str), result_rows (list of dicts), error (str or None).
    """
    try:
        df = _rows_to_df(rows)
        # Return dataframe info so the model can respond with structured analysis
        info = {
            "shape": df.shape,
            "columns": df.dtypes.astype(str).to_dict(),
            "sample": df.head(5).to_dict(orient="records"),
            "numeric_summary": df.describe().to_dict() if not df.select_dtypes(include="number").empty else {},
        }
        return {
            "dataframe_info": info,
            "total_rows": len(df),
            "analysis_description": analysis_description,
            "error": None,
        }
    except Exception as e:
        logger.error("run_python_analysis failed: %s", e)
        return {"dataframe_info": {}, "total_rows": 0, "error": str(e)}
