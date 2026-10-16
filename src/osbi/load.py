"""Загрузка сырых данных в DuckDB.

Слои:
  raw_*   — как пришло из источника (API, файлы ФФ и закупок), без изменений;
  ref_*   — справочники, которые ведёт аналитик (SKU, себестоимость, маппинг статей);
  stg_*   — очищенные и типизированные таблицы (sql/staging);
  mart_*  — витрины для дашборда и решений (sql/marts + python-модели).
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import duckdb
import pandas as pd

from .flatten import flatten_dir

ROOT = Path(__file__).resolve().parents[2]


def load_params(path: str | Path | None = None) -> dict:
    path = Path(path) if path else ROOT / "config" / "params.toml"
    with open(path, "rb") as f:
        return tomllib.load(f)


def _params_frame(params: dict) -> pd.DataFrame:
    flat = {}
    for section, values in params.items():
        for key, value in values.items():
            flat[f"{section}_{key}"] = value
    return pd.DataFrame([flat])


def load_all(con: duckdb.DuckDBPyConnection, data_dir: str | Path, ref_dir: str | Path, params: dict) -> None:
    data_dir, ref_dir = Path(data_dir), Path(ref_dir)

    con.register("params_df", _params_frame(params))
    con.execute("create or replace table params as select * from params_df")

    rows = flatten_dir(data_dir / "raw" / "accruals")
    acc = pd.DataFrame(rows, columns=["accrual_id", "date", "category", "unit_number", "sku",
                                      "type_id", "amount", "is_remainder"])
    acc["sku"] = acc["sku"].astype("Int64")
    acc["type_id"] = acc["type_id"].astype("Int64")
    con.register("acc_df", acc)
    con.execute("""
        create or replace table raw_accrual_lines as
        select accrual_id::bigint as accrual_id, date::date as date, category, unit_number,
               sku::bigint as sku, type_id::int as type_id, amount::double as amount, is_remainder
        from acc_df
    """)

    def csv(table: str, path: Path):
        con.execute(f"create or replace table {table} as select * from read_csv_auto(?, header=true)", [str(path)])

    csv("raw_postings", data_dir / "raw" / "postings.csv")
    csv("raw_returns", data_dir / "raw" / "returns.csv")
    csv("raw_stocks_ozon", data_dir / "raw" / "stocks_ozon.csv")
    csv("raw_supplies", data_dir / "raw" / "supplies.csv")
    csv("raw_performance_ads", data_dir / "raw" / "performance_ads.csv")
    csv("raw_ff_stocks", data_dir / "external" / "ff_stocks.csv")
    csv("raw_purchases", data_dir / "external" / "purchases.csv")
    csv("raw_realization_report", data_dir / "external" / "realization_report.csv")
    csv("ref_sku", ref_dir / "sku_master.csv")
    csv("ref_accrual_type_map", ref_dir / "accrual_type_map.csv")
