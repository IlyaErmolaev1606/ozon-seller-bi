"""Выгрузка витрин в CSV для Yandex DataLens (подключение «Файлы»).

Формат — под загрузчик DataLens: UTF-8, разделитель — запятая, десятичная точка,
месяц — строка ГГГГ-ММ (сортируется как дата и подходит для селектора).
Заголовки — по-русски: поля датасета сразу получают понятные имена.

Расходы выгружаются положительными числами. Доли (маржа, тарифы, ДРР) не выгружаем:
датасет считает их как отношение сумм, иначе при выборе нескольких SKU или месяцев
получится среднее долей вместо доли итога.

В одном подключении DataLens — не больше 10 файлов, поэтому витрины сведены в 9 CSV.
Инструкция по сборке дашборда — docs/08_datalens.md.

    python -m osbi.export_datalens           # из build/osbi.duckdb (после make demo)
    python -m osbi.export_datalens --demo    # сгенерировать демо-данные и собрать заново
"""
from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .dashboard import money_findings
from .load import ROOT
from .unit_economics import SCENARIOS

MAX_FILES_PER_CONNECTION = 10

SCENARIO_LABELS = {
    "rate_plus_3pp": "Ставка +3 п.п.",
    "deeper_discount_10": "Скидка Ozon −10% цены",
    "points_cap_50": "Баллы гасят 50%",
}

# Статьи для «куда уходит выручка»: группа, статья, выражение из mart_sku_month.
# Расход — со знаком «+», возврат денег от Ozon (баллы, компенсации) — со знаком «−»,
# вклад в прибыль — отдельной строкой. Сумма всех статей SKU × месяц = выручке.
# Номер в названии группы задаёт порядок слоёв в столбце: прибыль — у оси, чтобы месяцы сравнивались.
PNL_LINES = [
    ("1. Вклад в прибыль", "Вклад в прибыль", "contribution"),
    ("2. Себестоимость", "Себестоимость", "cogs"),
    ("2. Себестоимость", "Брак", "defect_writeoff"),
    ("2. Себестоимость", "Утраты на складе", "loss_writeoff"),
    ("3. Удержания Ozon", "Комиссия", "-commission"),
    ("3. Удержания Ozon", "Логистика", "-logistics"),
    ("3. Удержания Ozon", "Последняя миля", "-last_mile"),
    ("3. Удержания Ozon", "Обработка отправлений", "-processing"),
    ("3. Удержания Ozon", "Обратная логистика", "-reverse_logistics"),
    ("3. Удержания Ozon", "Эквайринг", "-acquiring"),
    ("3. Удержания Ozon", "Хранение", "-storage"),
    ("3. Удержания Ozon", "Платные отзывы", "-reviews"),
    ("3. Удержания Ozon", "Зачтено баллами", "-points_offset"),
    ("3. Удержания Ozon", "Компенсации Ozon", "-compensation"),
    ("4. Реклама", "Реклама", "ads"),
    ("5. Фулфилмент", "Фулфилмент", "ff_cost"),
    ("6. Налоги", "НДС", "vat"),
    ("6. Налоги", "Налог УСН", "income_tax"),
    ("7. Прочие удержания", "Начисления без SKU (распределены)", "overhead_alloc"),
]


def kpi_month(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        select month                                         as "Месяц",
               round(revenue, 2)                             as "Выручка",
               units_sold::bigint                            as "Продано шт",
               round(payout, 2)                              as "Выплата Ozon",
               round(ozon_fees_gross, 2)                     as "Удержания Ozon номинал",
               round(points_offset, 2)                       as "Зачтено баллами",
               round(ozon_fees_net, 2)                       as "Удержания Ozon факт",
               round(ads, 2)                                 as "Реклама",
               cogs_total::bigint                            as "Себестоимость с браком и утратами",
               round(contribution, 2)                        as "Вклад в прибыль",
               loss_making_skus                              as "Убыточных SKU",
               round(lag(revenue) over w, 2)                 as "Выручка пред месяц",
               round(lag(contribution) over w, 2)            as "Вклад в прибыль пред месяц"
        from mart_kpi_month
        window w as (order by month)
        order by month
    """).df()


def sku_month(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        select m.month                                       as "Месяц",
               m.offer_id                                    as "Артикул",
               m.model_id                                    as "Модель",
               m.category                                    as "Категория",
               m.name                                        as "Товар",
               coalesce(a.abc, '—')                          as "ABC",
               coalesce(a.xyz, '—')                          as "XYZ",
               case when m.ozon_discounts_enabled = 1 then 'включены' else 'отключены' end
                                                             as "Скидки Ozon",
               case when m.contribution >= 0 then 'Прибыль' else 'Убыток' end
                                                             as "Результат",
               m.units_sold                                  as "Продано шт",
               m.units_returned                              as "Возвраты шт",
               m.units_defect                                as "Брак шт",
               round(m.revenue, 2)                           as "Выручка",
               round(m.ozon_fees_gross, 2)                   as "Удержания Ozon номинал",
               round(m.points_offset, 2)                     as "Зачтено баллами",
               round(m.ozon_fees_net, 2)                     as "Удержания Ozon факт",
               round(m.compensation, 2)                      as "Компенсации Ozon",
               round(m.ads, 2)                               as "Реклама",
               m.cogs                                        as "Себестоимость",
               m.defect_writeoff + m.loss_writeoff           as "Брак и утраты",
               m.ff_cost                                     as "Фулфилмент",
               round(m.overhead_alloc, 2)                    as "Прочие удержания",
               round(m.vat + m.income_tax, 2)                as "Налоги",
               round(m.contribution, 2)                      as "Вклад в прибыль",
               round(m.points_earned, 2)                     as "Начислено баллов",
               round(m.points_unused, 2)                     as "Не использовано баллов"
        from mart_sku_month m
        left join mart_abc_xyz a using (sku)
        order by m.month, m.offer_id
    """).df()


def pnl_lines(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    selects = [f"""
            select month, sku, offer_id, model_id, category,
                   '{group}' as grp, '{line}' as line, {order} as line_order, {expr} as amount
            from mart_sku_month""" for order, (group, line, expr) in enumerate(PNL_LINES)]
    return con.execute(f"""
        with l as ({' union all '.join(selects)})
        select month                 as "Месяц",
               offer_id              as "Артикул",
               model_id              as "Модель",
               category              as "Категория",
               grp                   as "Группа",
               line                  as "Статья",
               round(amount, 2)      as "Сумма"
        from l
        where round(amount, 2) <> 0
        order by month, sku, line_order
    """).df()


def findings(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    fnd, _ = money_findings(con)
    cols = ["Тип", "Что", "Где", "rub", "Действие"]
    if fnd.empty:
        return pd.DataFrame(columns=["Тип", "Находка", "Где", "Сумма", "Действие"])
    out = fnd[cols].rename(columns={"Что": "Находка", "rub": "Сумма"})
    out["Сумма"] = out["Сумма"].round(2)
    return out.reset_index(drop=True)


def recon_realization(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        select r.month                    as "Месяц",
               s.offer_id                 as "Артикул",
               r.api_qty_sold             as "Продано по API",
               r.report_qty_sold          as "Продано по отчёту",
               r.diff_qty_sold            as "Расхождение шт",
               round(r.api_amount_sold, 2)     as "Сумма по API",
               round(r.report_amount_sold, 2)  as "Сумма по отчёту",
               r.diff_amount_sold         as "Расхождение суммы",
               r.api_qty_returned         as "Возвраты по API",
               r.report_qty_returned      as "Возвраты по отчёту",
               r.diff_qty_returned        as "Расхождение возвратов шт",
               r.diff_amount_returned     as "Расхождение возвратов сумма",
               r.status                   as "Статус"
        from mart_recon_realization r
        left join ref_sku s using (sku)
        order by r.month, s.offer_id
    """).df()


def recon_ads(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        select month                                  as "Месяц",
               finance_ads                            as "Реклама по финансам",
               performance_ads                        as "Реклама по Performance API",
               diff_rub                               as "Расхождение",
               coalesce(days_missing_in_performance, '—') as "Дни без данных Performance",
               status                                 as "Статус"
        from mart_recon_ads
        order by month
    """).df()


def dq(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        select check_name as "Проверка", detail as "Детали", status as "Статус", value_rub as "Сумма"
        from mart_dq
    """).df()


def purchase_plan(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute("""
        select model_id                               as "Модель",
               cards                                  as "Карточки",
               case when recommended_order_qty > 0 then 'Заказать'
                    when excess_units > 0 then 'Излишек'
                    else 'OK' end                     as "Статус",
               action                                 as "Действие",
               round(demand_daily, 1)                 as "Спрос в день",
               ozon_qty::bigint                       as "Остаток на Ozon",
               in_transit::bigint                     as "В пути на Ozon",
               ff_qty                                 as "Остаток на ФФ",
               on_order                               as "В заказе у поставщика",
               round(cover_days)::bigint              as "Запас дней",
               round(safety_stock)::bigint            as "Страховой запас",
               round(reorder_point)::bigint           as "Точка заказа",
               recommended_order_qty::bigint          as "Заказать шт",
               excess_units::bigint                   as "Излишек шт",
               round(excess_cost_rub, 2)              as "Излишек по себестоимости",
               round(excess_holding_rub_month, 2)     as "Хранение излишка в месяц",
               lead_time_days                         as "Срок поставки дней",
               moq                                    as "Минимальная партия",
               landed_cost_rub                        as "Себестоимость единицы",
               oos_eta                                as "Прогноз OOS",
               as_of                                  as "Дата расчёта"
        from mart_purchase_plan
        order by model_id
    """).df()


def scenarios(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    sc = con.execute("select * from mart_scenarios order by sku").df()
    rows = []
    for _, r in sc.iterrows():
        for key, label in SCENARIO_LABELS.items():
            rows.append({"Артикул": r["offer_id"], "Сценарий": label,
                         "Описание сценария": SCENARIOS[key],
                         "Изменение прибыли в месяц": round(float(r[f"{key}_delta_month"]), 2)})
    return pd.DataFrame(rows)


EXPORTS = {
    "kpi_month.csv": kpi_month,
    "sku_month.csv": sku_month,
    "pnl_lines.csv": pnl_lines,
    "findings.csv": findings,
    "recon_realization.csv": recon_realization,
    "recon_ads.csv": recon_ads,
    "dq.csv": dq,
    "purchase_plan.csv": purchase_plan,
    "scenarios.csv": scenarios,
}
assert len(EXPORTS) <= MAX_FILES_PER_CONNECTION


def check(frames: dict[str, pd.DataFrame]) -> None:
    """Инварианты, на которые опирается дашборд: статьи P&L в сумме дают выручку,
    вклад в прибыль из sku_month сходится с итогами месяца."""
    sku = frames["sku_month.csv"]
    lines = frames["pnl_lines.csv"].groupby(["Месяц", "Артикул"])["Сумма"].sum()
    rev = sku.set_index(["Месяц", "Артикул"])["Выручка"]
    gap = (lines.reindex(rev.index).fillna(0) - rev).abs().max()
    assert gap < 0.5, f"статьи P&L не сходятся с выручкой: {gap:.2f}"

    recomputed = (sku["Выручка"] - sku["Удержания Ozon факт"] + sku["Компенсации Ozon"] - sku["Реклама"]
                  - sku["Себестоимость"] - sku["Брак и утраты"] - sku["Фулфилмент"] - sku["Прочие удержания"]
                  - sku["Налоги"])
    gap = (recomputed - sku["Вклад в прибыль"]).abs().max()
    assert gap < 0.5, f"вклад в прибыль не собирается из статей: {gap:.2f}"

    kpi = frames["kpi_month.csv"].set_index("Месяц")
    by_month = sku.groupby("Месяц")[["Выручка", "Вклад в прибыль"]].sum()
    gap = (by_month - kpi[["Выручка", "Вклад в прибыль"]]).abs().to_numpy().max()
    assert gap < 1.0, f"sku_month не сходится с kpi_month: {gap:.2f}"


def export(con: duckdb.DuckDBPyConnection, out_dir: Path) -> dict[str, pd.DataFrame]:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = {name: fn(con) for name, fn in EXPORTS.items()}
    check(frames)
    for name, df in frames.items():
        df = df.replace([np.inf, -np.inf], np.nan)
        df.to_csv(out_dir / name, index=False, encoding="utf-8", lineterminator="\n")
    return frames


def main() -> None:
    ap = argparse.ArgumentParser(description="Выгрузка витрин в CSV для DataLens")
    ap.add_argument("--demo", action="store_true", help="сгенерировать демо-данные и собрать витрины заново")
    ap.add_argument("--db", default="build/osbi.duckdb")
    ap.add_argument("--out", default="datalens")
    args = ap.parse_args()
    db_path = ROOT / args.db
    if args.demo or not db_path.exists():
        from . import demo_data
        from .build import build
        data_dir = demo_data.generate(ROOT / "data" / "demo")
        con = build(data_dir, db_path, render=False)
    else:
        con = duckdb.connect(str(db_path), read_only=True)
    frames = export(con, ROOT / args.out)
    for name, df in frames.items():
        print(f"{args.out}/{name}: {len(df)} строк, {len(df.columns)} столбцов")


if __name__ == "__main__":
    main()
