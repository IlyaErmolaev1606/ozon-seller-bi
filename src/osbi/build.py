"""Сборка витрин: загрузка -> staging -> marts -> python-модели -> дашборд.

Запуск на демо-данных:
    python -m osbi.build --demo
На своих данных (после extract):
    python -m osbi.build --data data/live
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from . import demo_data
from .forecast import abc_xyz, cluster_supply_plan, oos_lost_sales, stock_plan
from .load import ROOT, load_all, load_params
from .unit_economics import SCENARIOS, UnitInputs, tariff_shock, unit_economics


def run_sql_dir(con: duckdb.DuckDBPyConnection, folder: Path) -> None:
    for path in sorted(folder.glob("*.sql")):
        con.execute(path.read_text(encoding="utf-8"))


def scenarios_frame(con: duckdb.DuckDBPyConnection, params: dict) -> pd.DataFrame:
    """Плановая модель, откалиброванная на факте месяца, и стресс-сценарии тарифов."""
    month = params["report"]["focus_month"]
    df = con.execute("""
        with p as (
            select sku,
                   avg(buyer_price) filter (where is_sold)  as buyer_price,
                   avg(seller_price) filter (where is_sold) as seller_price,
                   count(*) filter (where is_sold)::double
                       / nullif(count(*) filter (where is_sold or status = 'not_bought'), 0) as buyout_rate
            from stg_postings where strftime(delivered_date, '%Y-%m') = ? group by sku
        )
        select m.*, p.buyer_price, p.seller_price, p.buyout_rate, r.nominal_rate
        from mart_sku_month m join p using (sku) join ref_sku r using (sku)
        where m.month = ? and m.units_sold > 0
    """, [month, month]).df()
    rows = []
    for _, r in df.iterrows():
        sold = r["units_sold"]
        b = r["buyout_rate"] if r["buyout_rate"] and r["buyout_rate"] > 0 else 1.0
        delivered = sold / b                       # доставлено = выкуплено + невыкуплено
        x = UnitInputs(
            buyer_price=r["buyer_price"], seller_price=r["seller_price"], nominal_rate=r["nominal_rate"],
            logistics=-r["logistics"] / delivered, last_mile=-r["last_mile"] / delivered,
            landed_cost=r["landed_cost_rub"], ff_cost=r["ff_cost_per_unit"],
            storage_per_unit=-r["storage"] / sold, other_fees_per_unit=-r["reviews"] / sold,
            ads_rate=r["drr"] or 0.0, buyout_rate=b,
            return_rate=min(r["return_rate"] or 0.0, 0.99),
            defect_share=(r["units_defect"] / r["units_returned"]) if r["units_returned"] else 0.0,
            ozon_discounts_enabled=bool(r["ozon_discounts_enabled"]),
            vat_rate=params["tax"]["vat_rate_sales"], usn_rate=params["tax"]["usn_rate"],
        )
        base = unit_economics(x)
        row = {"sku": int(r["sku"]), "offer_id": r["offer_id"], "units_sold": int(sold),
               "margin_actual": r["margin_pct"], "margin_model": base["margin"],
               "profit_per_unit_model": base["profit"]}
        for key in SCENARIOS:
            s = tariff_shock(x, key)
            row[f"{key}_delta_month"] = s["delta_profit"] * sold
        rows.append(row)
    return pd.DataFrame(rows)


def build(data_dir: Path, db_path: Path, params_path: Path | None = None, render: bool = True) -> duckdb.DuckDBPyConnection:
    params = load_params(params_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    load_all(con, data_dir, ROOT / "data" / "reference", params)
    run_sql_dir(con, ROOT / "sql" / "staging")
    run_sql_dir(con, ROOT / "sql" / "marts")

    as_of = date.fromisoformat(params["report"]["as_of"])
    plan, purchase = stock_plan(con, params)
    con.register("plan_df", plan)
    con.execute("create or replace table mart_stock_plan as select * from plan_df")
    con.register("purchase_df", purchase)
    con.execute("create or replace table mart_purchase_plan as select * from purchase_df")
    cl = cluster_supply_plan(con, params, plan)
    con.register("cl_df", cl)
    con.execute("create or replace table mart_cluster_supply as select * from cl_df")
    oos = oos_lost_sales(con, params["report"]["focus_month"])
    con.register("oos_df", oos)
    con.execute("create or replace table mart_oos as select * from oos_df")
    ax = abc_xyz(con, as_of)
    con.register("ax_df", ax)
    con.execute("create or replace table mart_abc_xyz as select * from ax_df")
    sc = scenarios_frame(con, params)
    con.register("sc_df", sc)
    con.execute("create or replace table mart_scenarios as select * from sc_df")

    if render:
        from .dashboard import render_dashboard
        render_dashboard(con, params, ROOT / "dashboard" / "index.html")
    return con


def main() -> None:
    ap = argparse.ArgumentParser(description="Сборка витрин Ozon Seller BI")
    ap.add_argument("--demo", action="store_true", help="сгенерировать синтетические данные и собрать на них")
    ap.add_argument("--data", default="data/demo", help="папка с данными (raw/, external/)")
    ap.add_argument("--db", default="build/osbi.duckdb")
    ap.add_argument("--params", default=None)
    args = ap.parse_args()
    data_dir = ROOT / args.data
    if args.demo:
        demo_data.generate(data_dir)
    con = build(data_dir, ROOT / args.db, Path(args.params) if args.params else None)
    kpi = con.execute("select * from mart_kpi_month").df()
    print(kpi.to_string(index=False))
    print(f"\nДашборд: {ROOT / 'dashboard' / 'index.html'}")


if __name__ == "__main__":
    main()
