"""Прогноз спроса, потребность в закупке и поставках, OOS и затоваривание, ABC/XYZ.

Ключевые решения:
* Продажи во время OOS — это не спрос. Недельный спрос восстанавливаем по дням
  в наличии (sales / доля дней в наличии), иначе прогноз «учится» на дефиците
  и закрепляет его.
* Прогноз — метод Хольта с затухающим трендом: прозрачный, устойчивый на коротких
  рядах, объясняется собственнику за минуту. Сезонные модели — когда будет
  история хотя бы за 1,5–2 года.
* ABC — по вкладу в прибыль (contribution), а не по выручке: при разной глубине
  скидок Ozon выручка и прибыль ранжируют SKU по-разному.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd


def holt_damped(y: np.ndarray, alpha: float = 0.4, beta: float = 0.1, phi: float = 0.9,
                horizon: int = 8) -> tuple[np.ndarray, float]:
    """Затухающий тренд Хольта. Возвращает прогноз на horizon шагов и σ ошибки на шаг."""
    y = np.asarray(y, dtype=float)
    y = y[~np.isnan(y)]
    if len(y) == 0:
        return np.zeros(horizon), 0.0
    if len(y) == 1:
        return np.repeat(y[0], horizon), 0.0
    level, trend = y[0], y[1] - y[0]
    errors = []
    for t in range(1, len(y)):
        fc = level + phi * trend
        errors.append(y[t] - fc)
        new_level = alpha * y[t] + (1 - alpha) * (level + phi * trend)
        trend = beta * (new_level - level) + (1 - beta) * phi * trend
        level = new_level
    out, damp = [], 0.0
    for h in range(1, horizon + 1):
        damp += phi ** h
        out.append(max(level + damp * trend, 0.0))
    sigma = float(np.std(errors, ddof=1)) if len(errors) > 1 else 0.0
    return np.array(out), sigma


def weekly_demand(con: duckdb.DuckDBPyConnection, as_of: date) -> pd.DataFrame:
    """Недельный спрос по SKU с поправкой на дни без остатка."""
    df = con.execute("""
        with days as (
            select s.date::date as date, s.sku, sum(s.qty) as stock_eod
            from raw_stocks_ozon s group by all
        ),
        sales as (
            select created_date as date, sku, count(*) as units
            from stg_postings where status <> 'cancelled' group by all
        ),
        d as (
            select d.date, d.sku,
                   coalesce(lag(d.stock_eod) over (partition by d.sku order by d.date), d.stock_eod) > 0 as in_stock,
                   coalesce(s.units, 0) as units
            from days d left join sales s using (date, sku)
            where d.date <= ?
        )
        select date_trunc('week', date)::date as week, sku,
               sum(units) as units, avg(in_stock::int) as in_stock_share, count(*) as n_days
        from d group by all order by sku, week
    """, [as_of]).df()
    df = df[df["n_days"] == 7].copy()             # только полные недели
    df["demand"] = np.where(df["in_stock_share"] >= 0.3, df["units"] / df["in_stock_share"].clip(lower=1e-9),
                            np.nan)
    return df


def stock_plan(con: duckdb.DuckDBPyConnection, params: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Карточки: запас на складах Ozon и поставки. Модели: закупка, OOS-риск, затоваривание."""
    as_of = date.fromisoformat(params["report"]["as_of"])
    pl = params["planning"]
    wk = weekly_demand(con, as_of)
    ref = con.execute("select * from ref_sku").df().set_index("sku")

    ozon = con.execute("select sku, sum(qty) as ozon_qty from raw_stocks_ozon where date::date = ? group by sku",
                       [as_of]).df().set_index("sku")["ozon_qty"]
    transit = con.execute("""
        select sku, sum(shipped_qty) as qty from raw_supplies
        where shipped_date::date <= ? and accepted_date::date > ? group by sku""", [as_of, as_of]
    ).df().set_index("sku")["qty"]
    ff = con.execute("select model_id, qty from raw_ff_stocks where date::date = ?", [as_of]).df() \
        .set_index("model_id")["qty"]
    on_order = con.execute("""
        select model_id, sum(qty) as qty, min(eta_date::date) as next_eta from raw_purchases
        where eta_date::date > ? group by model_id""", [as_of]).df().set_index("model_id")

    rows = []
    for sku, g in wk.groupby("sku"):
        y = g.sort_values("week")["demand"].to_numpy()[-12:]
        fc, sigma_w = holt_damped(y)
        rows.append({"sku": sku, "demand_daily": fc[:4].mean() / 7, "sigma_weekly": sigma_w,
                     "in_stock_share_last_week": float(g.sort_values("week")["in_stock_share"].iloc[-1])})
    plan = pd.DataFrame(rows).set_index("sku")

    plan["model_id"] = [ref.at[k, "model_id"] for k in plan.index]
    plan["offer_id"] = [ref.at[k, "offer_id"] for k in plan.index]
    plan["ozon_qty"] = ozon.reindex(plan.index).fillna(0)
    plan["in_transit"] = transit.reindex(plan.index).fillna(0)
    # остаток ФФ хранится по модели (физически это один товар); делим между карточками по спросу
    model_dem = plan.groupby("model_id")["demand_daily"].transform("sum")
    plan["ff_share"] = (plan["demand_daily"] / model_dem.replace(0, np.nan)).fillna(0.0)
    ff_d = ff.to_dict()
    plan["ff_alloc"] = [ff_d.get(m, 0) for m in plan["model_id"]] * plan["ff_share"]
    plan["cover_days_ozon"] = np.where(plan["demand_daily"] > 0.05,
                                       (plan["ozon_qty"] + plan["in_transit"]) / plan["demand_daily"], np.inf)

    # ---- закупка: решение принимается по модели, а не по карточке ----
    oo_qty, oo_eta = on_order["qty"].to_dict(), on_order["next_eta"].to_dict()
    m = plan.groupby("model_id").agg(demand_daily=("demand_daily", "sum"),
                                     sigma_weekly=("sigma_weekly", lambda s: float(np.sqrt((s ** 2).sum()))),
                                     ozon_qty=("ozon_qty", "sum"), in_transit=("in_transit", "sum"),
                                     cards=("offer_id", lambda s: ", ".join(sorted(s))))
    m["ff_qty"] = [ff_d.get(k, 0) for k in m.index]
    m["on_order"] = [oo_qty.get(k, 0) for k in m.index]
    m["next_eta"] = [oo_eta.get(k) for k in m.index]
    first_sku = plan.reset_index().groupby("model_id")["sku"].first()
    m["lead_time_days"] = [ref.at[first_sku[k], "lead_time_days"] for k in m.index]
    m["moq"] = [ref.at[first_sku[k], "moq"] for k in m.index]
    m["landed_cost_rub"] = [ref.at[first_sku[k], "landed_cost_rub"] for k in m.index]
    lt_r = m["lead_time_days"] + pl["review_period_days"]
    m["safety_stock"] = pl["service_level_z"] * (m["sigma_weekly"] / math.sqrt(7)) * np.sqrt(lt_r)
    m["reorder_point"] = m["demand_daily"] * lt_r + m["safety_stock"]
    m["on_hand"] = m["ozon_qty"] + m["in_transit"] + m["ff_qty"]
    m["position"] = m["on_hand"] + m["on_order"]
    m["cover_days"] = np.where(m["demand_daily"] > 0.05, m["on_hand"] / m["demand_daily"], np.inf)
    need = (m["reorder_point"] - m["position"]).clip(lower=0)
    m["recommended_order_qty"] = np.where(need > 0, np.maximum(np.ceil(need), m["moq"]), 0)
    m["excess_units"] = np.floor((m["position"] - m["demand_daily"] * pl["overstock_cover_days"]).clip(lower=0))
    m["excess_cost_rub"] = m["excess_units"] * m["landed_cost_rub"]
    m["excess_holding_rub_month"] = m["excess_units"] * pl["ff_storage_rub_per_unit_day"] * 30
    m["days_to_next_eta"] = (pd.to_datetime(m["next_eta"]) - pd.Timestamp(as_of)).dt.days
    m["oos_eta"] = [(as_of + timedelta(days=int(c))).isoformat() if np.isfinite(c) else None
                    for c in m["cover_days"]]

    def model_action(r) -> str:
        acts = []
        if r["cover_days"] < r["lead_time_days"] and (pd.isna(r["days_to_next_eta"]) or
                                                      r["days_to_next_eta"] > r["cover_days"]):
            acts.append(f"Риск OOS ~{r['oos_eta']} до прихода закупки")
        if r["recommended_order_qty"] > 0:
            acts.append(f"Заказать {int(r['recommended_order_qty'])} шт.")
        if r["excess_units"] > 0:
            acts.append(f"Лишние {int(r['excess_units'])} шт. сверх {pl['overstock_cover_days']} дн.: "
                        f"стоп закупки, распродажа")
        return "; ".join(acts) if acts else "OK"

    m["action"] = m.apply(model_action, axis=1)

    def card_action(r) -> str:
        if r["ozon_qty"] + r["in_transit"] == 0 and r["ff_alloc"] > 0:
            return "Нет на складах Ozon при остатке на ФФ — срочная поставка"
        if r["cover_days_ozon"] < 7 and r["ff_alloc"] > 0:
            return "Запас на Ozon < 7 дней — поставка"
        return "OK"

    plan["action"] = plan.apply(card_action, axis=1)
    plan = plan.reset_index()
    plan["as_of"] = as_of
    m = m.reset_index()
    m["as_of"] = as_of
    return plan, m


def cluster_supply_plan(con: duckdb.DuckDBPyConnection, params: dict, plan: pd.DataFrame) -> pd.DataFrame:
    """Сколько отгрузить в каждый кластер: спрос покупателей кластера × целевой запас − остаток."""
    as_of = date.fromisoformat(params["report"]["as_of"])
    target_days = params["planning"]["target_cover_days_ozon"]
    share = con.execute("""
        select sku, cluster_to as cluster, count(*) as n
        from stg_postings where status <> 'cancelled' and created_date > ?::date - interval 28 day
        group by all""", [as_of]).df()
    share["share"] = share["n"] / share.groupby("sku")["n"].transform("sum")
    stock = con.execute("select sku, cluster, qty from raw_stocks_ozon where date::date = ?", [as_of]).df()
    transit = con.execute("""
        select sku, cluster, sum(shipped_qty) as in_transit from raw_supplies
        where shipped_date::date <= ? and accepted_date::date > ? group by all""", [as_of, as_of]).df()
    df = share.merge(stock, on=["sku", "cluster"], how="left").merge(transit, on=["sku", "cluster"], how="left")
    df = df.merge(plan[["sku", "demand_daily", "ff_alloc"]], on="sku", how="left").fillna(
        {"qty": 0, "in_transit": 0, "ff_alloc": 0})
    df["target_qty"] = np.ceil(df["demand_daily"] * df["share"] * target_days)
    df["need_qty"] = (df["target_qty"] - df["qty"] - df["in_transit"]).clip(lower=0)
    # не больше, чем есть на ФФ: режем пропорционально
    tot = df.groupby("sku")["need_qty"].transform("sum")
    scale = np.where(tot > df["ff_alloc"], df["ff_alloc"] / tot.replace(0, np.nan), 1.0)
    df["ship_qty"] = np.floor(df["need_qty"] * np.nan_to_num(scale, nan=0.0))
    df["cover_days_cluster"] = np.where(df["demand_daily"] * df["share"] > 0,
                                        (df["qty"] + df["in_transit"]) / (df["demand_daily"] * df["share"]), np.inf)
    return df[["sku", "cluster", "share", "qty", "in_transit", "target_qty", "ship_qty", "cover_days_cluster"]]


def oos_lost_sales(con: duckdb.DuckDBPyConnection, month: str) -> pd.DataFrame:
    """Дни без остатка на всех складах Ozon и оценка упущенных продаж.

    Оценка — верхняя граница: часть покупателей уходит на соседнюю карточку той же модели.
    """
    return con.execute("""
        with daily as (
            select s.date::date as date, s.sku, sum(s.qty) as stock_eod
            from raw_stocks_ozon s group by all
        ),
        sales as (
            select created_date as date, sku, count(*) as units
            from stg_postings where status <> 'cancelled' group by all
        ),
        d as (
            select d.date, d.sku, coalesce(s.units, 0) as units,
                   lag(d.stock_eod) over (partition by d.sku order by d.date) as stock_bod
            from daily d left join sales s using (date, sku)
        ),
        -- темп продаж при «здоровом» остатке (≥ 14 дней медианных продаж): при частичном
        -- наличии продажи тоже занижены, поэтому такие дни в оценку темпа не берём
        med as (
            select sku, median(units) as med_units from d where stock_bod > 0 group by sku
        ),
        rate as (
            select sku, avg(units) as rate_in_stock from (
                select d.sku, d.units, row_number() over (partition by d.sku order by d.date desc) as rn
                from d join med using (sku)
                where d.stock_bod > 0 and d.stock_bod >= 14 * med.med_units
                  and d.date <= last_day(strptime(? || '-01', '%Y-%m-%d'))
            ) where rn <= 28
            group by sku
        ),
        agg as (
            select d.sku, count(*) filter (where stock_bod = 0) as oos_days
            from d where strftime(d.date, '%Y-%m') = ?
            group by d.sku
        )
        select a.sku, r.offer_id, r.model_id, a.oos_days,
               round(a.oos_days * coalesce(rt.rate_in_stock, 0)) as lost_units_est,
               round(a.oos_days * coalesce(rt.rate_in_stock, 0) * p.buyer_price) as lost_revenue_est
        from agg a
        join ref_sku r on r.sku = a.sku
        left join rate rt on rt.sku = a.sku
        left join (select sku, avg(buyer_price) as buyer_price from stg_postings group by sku) p on p.sku = a.sku
        where a.oos_days > 0
        order by lost_revenue_est desc
    """, [month, month]).df()


def abc_xyz(con: duckdb.DuckDBPyConnection, as_of: date, months: int = 3,
            x_cv: float = 0.25, y_cv: float = 0.5) -> pd.DataFrame:
    sku_m = con.execute("""
        select sku, sum(contribution) as contribution, sum(revenue) as revenue
        from mart_sku_month
        where month > strftime(?::date - interval (?) month, '%Y-%m')
        group by sku""", [as_of, months]).df()
    pos = sku_m[sku_m["contribution"] > 0].sort_values("contribution", ascending=False).copy()
    pos["cum_share"] = pos["contribution"].cumsum() / pos["contribution"].sum()
    pos["abc"] = np.where(pos["cum_share"] <= 0.8, "A", np.where(pos["cum_share"] <= 0.95, "B", "C"))
    # первый SKU всегда A, даже если он один даёт > 80%
    if len(pos):
        pos.iloc[0, pos.columns.get_loc("abc")] = "A"
    neg = sku_m[sku_m["contribution"] <= 0].copy()
    neg["abc"] = "Убыток"
    res = pd.concat([pos, neg], ignore_index=True)
    wk = weekly_demand(con, as_of)
    cv = wk.groupby("sku")["demand"].apply(lambda s: s.dropna().tail(12).std(ddof=1) / s.dropna().tail(12).mean()
                                           if s.dropna().tail(12).mean() > 0 else np.nan)
    res["cv"] = res["sku"].map(cv)
    res["xyz"] = np.where(res["cv"] < x_cv, "X", np.where(res["cv"] < y_cv, "Y", "Z"))
    res["revenue_rank"] = res["revenue"].rank(ascending=False, method="min").astype(int)
    res["contribution_rank"] = res["contribution"].rank(ascending=False, method="min").astype(int)
    return res[["sku", "revenue", "contribution", "abc", "cv", "xyz", "revenue_rank", "contribution_rank"]]
