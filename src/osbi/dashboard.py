"""Статичный HTML-дашборд из витрин (прототип того, что в проде живёт в DataLens).

Один файл без внешних зависимостей: можно открыть локально или опубликовать
на GitHub Pages. Графики — inline SVG, подсказки — по наведению.
"""
from __future__ import annotations

import html
import math
from pathlib import Path

import duckdb
import pandas as pd


# ---------- форматирование ----------
def rub(x: float, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    a = abs(x)
    sign = "−" if x < 0 else ""
    if a >= 1e6:
        s = f"{a / 1e6:.{digits}f}".replace(".", ",") + " млн ₽"
    elif a >= 1e3:
        s = f"{a / 1e3:.0f} тыс. ₽"
    else:
        s = f"{a:.0f} ₽"
    return sign + s


def num(x: float) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    return f"{x:,.0f}".replace(",", " ")


def pct(x: float, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    s = f"{abs(x) * 100:.{digits}f}".replace(".", ",") + "%"
    return ("−" if x < 0 else "") + s


def esc(s) -> str:
    return html.escape(str(s))


# ---------- графики ----------
def diverging_bars(df: pd.DataFrame, label_col: str, value_col: str, tip_col: str, width: int = 520) -> str:
    """Горизонтальные бары от нуля: плюс — синий, минус — красный (полярность)."""
    row_h, bar_h, label_w, right_pad = 30, 16, 64, 78
    left = label_w + (78 if df[value_col].min() < 0 else 8)   # место под подпись отрицательных значений
    n = len(df)
    h = n * row_h + 30
    vmax = max(df[value_col].max(), 0)
    vmin = min(df[value_col].min(), 0)
    span = (vmax - vmin) or 1
    plot_w = width - left - right_pad
    x0 = left + plot_w * (-vmin / span)

    def sx(v):
        return left + plot_w * ((v - vmin) / span)

    parts = [f'<svg viewBox="0 0 {width} {h}" class="chart" role="img" aria-label="Вклад в прибыль по SKU">']
    # сетка: 0 и края
    ticks = _nice_ticks(vmin, vmax, 4)
    for t in ticks:
        x = sx(t)
        parts.append(f'<line x1="{x:.1f}" y1="6" x2="{x:.1f}" y2="{h - 22}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="{h - 6}" class="tick" text-anchor="middle">{esc(rub(t, 1))}</text>')
    parts.append(f'<line x1="{x0:.1f}" y1="6" x2="{x0:.1f}" y2="{h - 22}" class="axis"/>')
    for i, (_, r) in enumerate(df.iterrows()):
        y = 8 + i * row_h
        v = r[value_col]
        xa, xb = sorted([x0, sx(v)])
        w = max(xb - xa, 1)
        cls = "pos" if v >= 0 else "neg"
        rad = min(4, w / 2)
        # скругление только у конца бара (не у нуля)
        if v >= 0:
            path = (f"M{xa:.1f},{y} H{xb - rad:.1f} Q{xb:.1f},{y} {xb:.1f},{y + rad} V{y + bar_h - rad} "
                    f"Q{xb:.1f},{y + bar_h} {xb - rad:.1f},{y + bar_h} H{xa:.1f} Z")
        else:
            path = (f"M{xb:.1f},{y} H{xa + rad:.1f} Q{xa:.1f},{y} {xa:.1f},{y + rad} V{y + bar_h - rad} "
                    f"Q{xa:.1f},{y + bar_h} {xa + rad:.1f},{y + bar_h} H{xb:.1f} Z")
        tip = esc(r[tip_col])
        parts.append(f'<g class="hit" data-tip="{tip}"><rect x="0" y="{y - 6}" width="{width}" height="{row_h}" '
                     f'class="hitbox"/><path d="{path}" class="{cls}"/>')
        parts.append(f'<text x="4" y="{y + bar_h - 4}" class="lbl" text-anchor="start">{esc(r[label_col])}</text>')
        tx = (xb + 6) if v >= 0 else (xa - 6)
        anchor = "start" if v >= 0 else "end"
        parts.append(f'<text x="{tx:.1f}" y="{y + bar_h - 4}" class="val" text-anchor="{anchor}">{esc(rub(v))}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def dumbbell(df: pd.DataFrame, label_col: str, a_col: str, b_col: str, tip_col: str, width: int = 520) -> str:
    """Номинальная vs фактическая доля удержаний Ozon (до и после баллов)."""
    row_h, left, right_pad = 30, 72, 24
    n = len(df)
    h = n * row_h + 30
    vmax = max(df[a_col].max(), df[b_col].max()) * 1.05
    ticks = _nice_ticks(0, vmax, 5)
    vmax = max(ticks[-1], vmax)
    plot_w = width - left - right_pad

    def sx(v):
        return left + plot_w * (v / vmax)

    parts = [f'<svg viewBox="0 0 {width} {h}" class="chart" role="img" aria-label="Доля удержаний Ozon до и после баллов">']
    for t in ticks:
        x = sx(t)
        parts.append(f'<line x1="{x:.1f}" y1="6" x2="{x:.1f}" y2="{h - 22}" class="grid"/>')
        parts.append(f'<text x="{x:.1f}" y="{h - 6}" class="tick" text-anchor="middle">{pct(t, 0)}</text>')
    for i, (_, r) in enumerate(df.iterrows()):
        y = 8 + i * row_h + 8
        xa, xb = sx(r[a_col]), sx(r[b_col])
        tip = esc(r[tip_col])
        parts.append(f'<g class="hit" data-tip="{tip}"><rect x="0" y="{y - 14}" width="{width}" height="{row_h}" class="hitbox"/>')
        parts.append(f'<line x1="{min(xa, xb):.1f}" y1="{y}" x2="{max(xa, xb):.1f}" y2="{y}" class="connector"/>')
        parts.append(f'<circle cx="{xa:.1f}" cy="{y}" r="5" class="dot-nominal"/>')
        parts.append(f'<circle cx="{xb:.1f}" cy="{y}" r="5" class="dot-effective"/>')
        parts.append(f'<text x="4" y="{y + 4}" class="lbl" text-anchor="start">{esc(r[label_col])}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def _nice_ticks(lo: float, hi: float, n: int) -> list[float]:
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=mag * 10)
    start = math.floor(lo / step) * step
    ticks, t = [], start
    while t <= hi + 1e-9:
        if t >= lo - 1e-9:
            ticks.append(round(t, 10))
        t += step
    return ticks


def table(df: pd.DataFrame, cols: list[tuple[str, str, callable]], cls: str = "") -> str:
    head = "".join(f'<th class="{"num" if f not in (None, str) else ""}">{esc(t)}</th>' for _, t, f in cols)
    body = []
    for _, r in df.iterrows():
        tds = []
        for c, _, f in cols:
            v = r[c]
            if f in (None, str):
                tds.append(f"<td>{esc('' if v is None or (isinstance(v, float) and math.isnan(v)) else v)}</td>")
            else:
                tds.append(f'<td class="num">{esc(f(v))}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    return f'<div class="tablewrap"><table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def status_badge(s: str) -> str:
    kind = "ok" if s == "OK" or s == "Возмещено" else ("warn" if s in ("Проверить",) else "bad")
    icon = {"ok": "✓", "warn": "!", "bad": "✕"}[kind]
    return f'<span class="badge {kind}"><span aria-hidden="true">{icon}</span> {esc(s)}</span>'


# ---------- находки ----------
def money_findings(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, float]:
    """Где теряются деньги — в рублях. Общий источник для HTML-дашборда и выгрузки в DataLens.

    Возвращает таблицу находок (Тип, Что, Где, rub, Действие) и сумму к претензиям Ozon:
    утраты без возмещения + недостачи при приёмке.
    """
    losses = con.execute("select * from mart_stock_losses order by claim_gap_rub desc").df()
    accept = con.execute("select * from mart_supply_acceptance where status <> 'OK'").df()
    recon = con.execute("select * from mart_recon_realization where status <> 'OK' order by month").df()
    oos = con.execute("select * from mart_oos").df()

    findings = []
    gaps = losses[losses["claim_gap_rub"] > 1]
    if len(gaps):
        findings.append({"Тип": "Претензия к Ozon", "Что": "Утрата на складе Ozon без возмещения",
                         "Где": f"{', '.join(sorted(set(gaps['clusters'])))}, {gaps['first_loss_date'].min():%d.%m}: "
                                f"{len(gaps)} SKU, {int(gaps['lost_qty'].sum())} шт.",
                         "rub": gaps["claim_gap_rub"].sum(),
                         "Действие": "Запросить расчёт возмещения (лимит по формуле Ozon), претензия"})
    for _, r in accept.iterrows():
        findings.append({"Тип": "Претензия к Ozon", "Что": "Недостача при приёмке поставки",
                         "Где": f"{r['supply_id']}, SKU {r['sku']}, {r['cluster']}",
                         "rub": -r["diff_cost_rub"], "Действие": "Акт расхождений, претензия"})
    for _, r in recon.iterrows():
        findings.append({"Тип": "Сверка с отчётом", "Что": "Отчёт о реализации ≠ API", "Где": f"{r['month']}, SKU {r['sku']}",
                         "rub": abs(r["diff_amount_sold"]) + abs(r["diff_amount_returned"]),
                         "Действие": "Сверка по отправлениям, запрос в поддержку"})
    for _, r in oos.iterrows():
        findings.append({"Тип": "Упущенная выручка", "Что": "Упущенные продажи: карточка без остатка",
                         "Где": f"{r['offer_id']} ({r['model_id']}), {r['oos_days']} дн.",
                         "rub": r["lost_revenue_est"], "Действие": "Поставка на Ozon из остатка ФФ"})
    fnd = pd.DataFrame(findings).sort_values("rub", ascending=False) if findings else pd.DataFrame()
    claims_total = losses["claim_gap_rub"].clip(lower=0).sum() + (-accept["diff_cost_rub"]).sum()
    return fnd, claims_total


# ---------- сборка ----------
def render_dashboard(con: duckdb.DuckDBPyConnection, params: dict, out_path: Path) -> Path:
    month = params["report"]["focus_month"]
    kpi = con.execute("select * from mart_kpi_month order by month").df()
    cur = kpi[kpi["month"] == month].iloc[0]
    prev = kpi[kpi["month"] < month].iloc[-1] if (kpi["month"] < month).any() else None

    sku = con.execute("""
        select m.*, a.abc, a.xyz from mart_sku_month m left join mart_abc_xyz a using (sku)
        where m.month = ? order by contribution desc""", [month]).df()
    sku["label"] = sku["offer_id"] + " · " + sku["name"].str.slice(0, 22)
    sku["short"] = sku["offer_id"]
    sku["tip"] = sku.apply(lambda r: (f"{r['offer_id']} {r['name']}: вклад {rub(r['contribution'])}, "
                                      f"маржа {pct(r['margin_pct'])}, выручка {rub(r['revenue'])}, "
                                      f"продано {num(r['units_sold'])} шт."), axis=1)
    sku["tip2"] = sku.apply(lambda r: (f"{r['offer_id']}: номинально {pct(r['nominal_take_rate'])} выручки, "
                                       f"после баллов {pct(r['effective_take_rate'])}; "
                                       f"баллов зачтено {rub(r['points_offset'])}, не использовано {rub(r['points_unused'])}"),
                           axis=1)

    ads = con.execute("select * from mart_recon_ads order by month").df()
    dq = con.execute("select * from mart_dq").df()
    purchase = con.execute("select * from mart_purchase_plan where action <> 'OK' order by excess_cost_rub desc").df()
    cards = con.execute("select * from mart_stock_plan where action <> 'OK'").df()
    scen = con.execute("select * from mart_scenarios order by sku").df()
    cl = con.execute("""
        select cluster, sum(qty) as qty, sum(ship_qty) as ship_qty from mart_cluster_supply group by cluster
        order by ship_qty desc""").df()
    local = con.execute("""
        select avg(is_local::int) as local_share from stg_postings
        where status <> 'cancelled' and strftime(created_date, '%Y-%m') = ?""", [month]).fetchone()[0]

    fnd, claims_total = money_findings(con)

    loss_skus = sku[sku["contribution"] < 0]
    worst = loss_skus.sort_values("contribution").head(2)
    headline = []
    for _, r in worst.iterrows():
        why = ("скидки Ozon отключены — тариф по номиналу" if not r["ozon_discounts_enabled"]
               else "баллы покрывают малую часть тарифа")
        headline.append(f"{esc(r['offer_id'])} теряет {esc(rub(-r['contribution']))} в месяц ({why})")

    def delta(cur_v, prev_v, good_up=True):
        if prev is None or not prev_v:
            return ""
        d = (cur_v - prev_v) / abs(prev_v)
        up = d >= 0
        good = up == good_up
        arrow = "▲" if up else "▼"
        return f'<span class="delta {"good" if good else "bad"}">{arrow} {pct(abs(d))} к пред. месяцу</span>'

    tiles = f"""
    <div class="kpis">
      <div class="tile hero"><div class="tl">Вклад в прибыль, {esc(month)}</div>
        <div class="tv">{esc(rub(cur['contribution'], 2))}</div>
        <div class="ts">маржа {pct(cur['margin_pct'])} · {delta(cur['contribution'], prev['contribution'] if prev is not None else None)}</div></div>
      <div class="tile"><div class="tl">Выручка (цена реализации)</div><div class="tv">{esc(rub(cur['revenue'], 1))}</div>
        <div class="ts">{num(cur['units_sold'])} шт. · {delta(cur['revenue'], prev['revenue'] if prev is not None else None)}</div></div>
      <div class="tile"><div class="tl">Удержания Ozon фактически</div><div class="tv">{pct(cur['effective_take_rate'])}</div>
        <div class="ts">номинально {pct(cur['nominal_take_rate'], 0)} выручки — разницу закрыли баллы</div></div>
      <div class="tile"><div class="tl">ДРР</div><div class="tv">{pct(cur['drr'])}</div>
        <div class="ts">реклама {esc(rub(cur['ads']))}</div></div>
      <div class="tile"><div class="tl">К претензиям Ozon</div><div class="tv">{esc(rub(claims_total))}</div>
        <div class="ts">утраты без возмещения + недостачи приёмки</div></div>
    </div>"""

    sku_tbl = table(sku, [
        ("offer_id", "Артикул", str), ("name", "Товар", str),
        ("units_sold", "Шт.", num), ("revenue", "Выручка", rub),
        ("nominal_take_rate", "Удерж. номинал", pct), ("effective_take_rate", "Удерж. факт", pct),
        ("drr", "ДРР", pct), ("return_rate", "Возвраты", pct),
        ("contribution", "Вклад", rub), ("margin_pct", "Маржа", pct),
        ("abc", "ABC", str), ("xyz", "XYZ", str)], "sku")

    find_tbl = table(fnd.rename(columns={"rub": "Сумма"}), [
        ("Что", "Находка", str), ("Где", "Где", str), ("Сумма", "Сумма", rub), ("Действие", "Действие", str)]) \
        if len(fnd) else "<p>Расхождений нет.</p>"

    purchase_tbl = table(purchase, [
        ("model_id", "Модель", str), ("cards", "Карточки", str), ("demand_daily", "Спрос/день", lambda v: f"{v:.1f}".replace(".", ",")),
        ("cover_days", "Запас, дней", num), ("excess_cost_rub", "Лишний запас", rub), ("action", "Действие", str)])
    cards_tbl = table(cards, [("offer_id", "Карточка", str), ("ozon_qty", "На Ozon", num),
                              ("ff_alloc", "Доступно на ФФ", num), ("action", "Действие", str)])
    cl_tbl = table(cl, [("cluster", "Кластер", str), ("qty", "Остаток", num), ("ship_qty", "Отгрузить", num)])

    scen_tot = {k: scen[f"{k}_delta_month"].sum() for k in ("rate_plus_3pp", "deeper_discount_10", "points_cap_50")}
    scen_tbl = table(scen, [("offer_id", "Артикул", str), ("margin_actual", "Маржа факт", pct),
                            ("margin_model", "Маржа модель", pct),
                            ("rate_plus_3pp_delta_month", "Ставка +3 п.п.", rub),
                            ("deeper_discount_10_delta_month", "Скидка Ozon −10% цены", rub),
                            ("points_cap_50_delta_month", "Баллы гасят 50%", rub)])

    dq_rows = "".join(f"<tr><td>{esc(r['check_name'])}</td><td>{esc(r['detail'])}</td>"
                      f"<td>{status_badge(r['status'])}</td><td class='num'>{esc(rub(r['value_rub']) if pd.notna(r['value_rub']) else '')}</td></tr>"
                      for _, r in dq.iterrows())
    ads_rows = "".join(f"<tr><td>{esc(r['month'])}</td><td class='num'>{esc(rub(r['finance_ads']))}</td>"
                       f"<td class='num'>{esc(rub(r['performance_ads']))}</td><td class='num'>{esc(pct(r['diff_pct']))}</td>"
                       f"<td>{status_badge(r['status'])}</td></tr>" for _, r in ads.iterrows())
    kpi_tbl = table(kpi, [("month", "Месяц", str), ("revenue", "Выручка", rub), ("payout", "К выплате от Ozon", rub),
                          ("effective_take_rate", "Удержания факт", pct), ("drr", "ДРР", pct),
                          ("contribution", "Вклад в прибыль", rub), ("margin_pct", "Маржа", pct),
                          ("loss_making_skus", "Убыточных SKU", num)])

    bars = diverging_bars(sku.sort_values("contribution", ascending=False), "short", "contribution", "tip")
    dumb = dumbbell(sku.sort_values("nominal_take_rate", ascending=False), "short",
                    "nominal_take_rate", "effective_take_rate", "tip2")

    page = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ozon Seller BI — демо</title>
<style>
:root {{
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --pos: #2a78d6; --neg: #e34948; --nominal: #86b6ef; --effective: #184f95;
  --good: #006300; --bad: #d03b3b; --warn-bg: #fff4d6; --bad-bg: #fde8e8; --ok-bg: #e6f4e6;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --pos: #3987e5; --neg: #e66767; --nominal: #1c5cab; --effective: #9ec5f4;
    --good: #0ca30c; --bad: #e66767; --warn-bg: #3a3214; --bad-bg: #3a1c1c; --ok-bg: #16301a;
  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --pos: #3987e5; --neg: #e66767; --nominal: #1c5cab; --effective: #9ec5f4;
  --good: #0ca30c; --bad: #e66767; --warn-bg: #3a3214; --bad-bg: #3a1c1c; --ok-bg: #16301a;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1120px; margin: 0 auto; padding: 24px 16px 64px; }}
header h1 {{ font-size: 24px; margin: 0 0 4px; font-weight: 650; }}
header p {{ margin: 0; color: var(--ink2); }}
.note {{ margin-top: 12px; padding: 10px 14px; border: 1px solid var(--border); border-radius: 10px;
  background: var(--surface); color: var(--ink2); font-size: 14px; }}
section {{ margin-top: 28px; }}
h2 {{ font-size: 18px; margin: 0 0 4px; font-weight: 650; }}
.sub {{ color: var(--ink2); margin: 0 0 12px; font-size: 14px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }}
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 860px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
.kpis {{ display: grid; grid-template-columns: 1.6fr repeat(4, 1fr); gap: 12px; margin-top: 20px; }}
@media (max-width: 860px) {{ .kpis {{ grid-template-columns: 1fr 1fr; }} .tile.hero {{ grid-column: 1 / -1; }} }}
.tile {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }}
.tl {{ color: var(--ink2); font-size: 13px; }}
.tv {{ font-size: 26px; font-weight: 650; margin-top: 2px; white-space: nowrap; }}
@media (max-width: 480px) {{ .tv {{ font-size: 21px; }} }}
.hero .tv {{ font-size: 44px; line-height: 1.1; white-space: nowrap; }}
.ts {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
.delta.good {{ color: var(--good); }} .delta.bad {{ color: var(--bad); }}
.headline {{ margin: 16px 0 0; padding: 12px 16px 12px 34px; border-left: 3px solid var(--neg); background: var(--surface);
  border-radius: 0 10px 10px 0; }}
.headline li {{ margin: 2px 0; }}
.chart {{ width: 100%; height: auto; display: block; }}
.chart .grid {{ stroke: var(--grid); stroke-width: 1; }}
.chart .axis {{ stroke: var(--axis); stroke-width: 1; }}
.chart .tick {{ fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }}
.chart .lbl {{ fill: var(--ink2); font-size: 12px; }}
.chart .val {{ fill: var(--ink); font-size: 12px; font-variant-numeric: tabular-nums; }}
.chart .pos {{ fill: var(--pos); }} .chart .neg {{ fill: var(--neg); }}
.chart .connector {{ stroke: var(--axis); stroke-width: 2; }}
.chart .dot-nominal {{ fill: var(--nominal); stroke: var(--surface); stroke-width: 2; }}
.chart .dot-effective {{ fill: var(--effective); stroke: var(--surface); stroke-width: 2; }}
.chart .hitbox {{ fill: transparent; }}
.chart .hit:hover .hitbox {{ fill: var(--grid); opacity: .45; }}
.legend {{ display: flex; gap: 16px; font-size: 13px; color: var(--ink2); margin: 0 0 8px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }}
.tablewrap {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
th, td {{ padding: 7px 8px; border-bottom: 1px solid var(--grid); text-align: left; vertical-align: top; }}
th {{ color: var(--ink2); font-weight: 600; white-space: nowrap; }}
table.sku td:nth-child(2) {{ min-width: 190px; }}
td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
.badge {{ display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12.5px; white-space: nowrap; }}
.badge.ok {{ background: var(--ok-bg); color: var(--good); }}
.badge.warn {{ background: var(--warn-bg); color: var(--ink); }}
.badge.bad {{ background: var(--bad-bg); color: var(--bad); }}
#tip {{ position: fixed; pointer-events: none; z-index: 10; max-width: 320px; padding: 8px 10px; border-radius: 8px;
  background: var(--ink); color: var(--surface); font-size: 12.5px; opacity: 0; transition: opacity .08s; }}
footer {{ margin-top: 40px; color: var(--muted); font-size: 13px; }}
</style></head>
<body><main>
<header>
  <h1>Ozon Seller BI — юнит-экономика, сверки, запасы</h1>
  <p>Прототип единого дашборда: от Seller API до решений собственника и финдиректора.</p>
  <div class="note"><b>Данные синтетические.</b> Товары, цены и события сгенерированы скриптом
  <code>osbi.demo_data</code> и не относятся ни к одной компании. В данные заложены типовые ситуации
  (OOS второй карточки, отключённые скидки, утрата на складе, расхождения отчётов), которые витрины находят сами.</div>
</header>

{tiles}
{('<ul class="headline">' + ''.join(f'<li>{h}</li>' for h in headline) + '</ul>') if headline else ''}

<section>
  <h2>Сколько на самом деле зарабатывает каждый артикул</h2>
  <p class="sub">Вклад в прибыль за {esc(month)}: выручка − все удержания Ozon (после баллов) − реклама − себестоимость −
  брак и утраты − фулфилмент − налоги. Наведите на строку.</p>
  <div class="grid2">
    <div class="card">{bars}</div>
    <div class="card">
      <div class="legend"><span><i style="background:var(--nominal)"></i>Номинальный тариф и услуги</span>
      <span><i style="background:var(--effective)"></i>После зачёта баллов</span></div>
      {dumb}
      <p class="sub" style="margin-top:8px">Номинальные удержания считаются от цены продавца и часто превышают выручку;
      баллы за скидки Ozon гасят до 99% услуг. Где точки совпадают — баллов нет.</p>
    </div>
  </div>
  <div class="card" style="margin-top:16px">{sku_tbl}</div>
</section>

<section>
  <h2>Где Ozon удерживает лишнее и где теряются деньги</h2>
  <p class="sub">Автоматические сверки: остатки по движениям, приёмка поставок, отчёт о реализации против API, упущенные продажи.</p>
  <div class="card">{find_tbl}</div>
</section>

<section>
  <h2>Что закупать, что выводить, куда везти</h2>
  <p class="sub">Решения по закупке — на уровне модели (физического товара), поставки на Ozon — по карточкам и кластерам.
  Доля локальных продаж в {esc(month)}: {pct(local)}.</p>
  <div class="grid2">
    <div class="card"><h3 style="margin:0 0 8px;font-size:15px">Закупка и затоваривание</h3>{purchase_tbl}</div>
    <div class="card"><h3 style="margin:0 0 8px;font-size:15px">Карточки и кластеры</h3>{cards_tbl}
      <div style="margin-top:12px">{cl_tbl}</div></div>
  </div>
</section>

<section>
  <h2>Что будет, если площадка поменяет правила</h2>
  <p class="sub">Изменение прибыли за месяц при текущих объёмах (эластичность спроса не учтена — это оценка эффекта на единицу × объём).
  Итого: ставка +3 п.п. {esc(rub(scen_tot['rate_plus_3pp']))}; скидка Ozon −10% цены {esc(rub(scen_tot['deeper_discount_10']))};
  баллы гасят 50% услуг {esc(rub(scen_tot['points_cap_50']))}.</p>
  <div class="card">{scen_tbl}</div>
</section>

<section>
  <h2>Можно ли верить цифрам</h2>
  <p class="sub">Контроль свежести и полноты каждого источника и сверка рекламы: финансы vs Performance API.</p>
  <div class="grid2">
    <div class="card"><div class="tablewrap"><table><thead><tr><th>Проверка</th><th>Детали</th><th>Статус</th><th class="num">Сумма</th></tr></thead>
      <tbody>{dq_rows}</tbody></table></div></div>
    <div class="card"><div class="tablewrap"><table><thead><tr><th>Месяц</th><th class="num">Финансы</th><th class="num">Performance API</th>
      <th class="num">Разница</th><th>Статус</th></tr></thead><tbody>{ads_rows}</tbody></table></div></div>
  </div>
</section>

<section>
  <h2>Динамика по месяцам</h2>
  <div class="card">{kpi_tbl}</div>
</section>

<footer>Собрано из витрин DuckDB · модель и допущения — в docs/ репозитория.</footer>
</main>
<div id="tip" role="tooltip"></div>
<script>
(() => {{
  const tip = document.getElementById('tip');
  document.querySelectorAll('.hit').forEach(g => {{
    g.addEventListener('mousemove', e => {{
      tip.textContent = g.dataset.tip; tip.style.opacity = 1;
      const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
      tip.style.left = x + 'px'; tip.style.top = (e.clientY + 14) + 'px';
    }});
    g.addEventListener('mouseleave', () => tip.style.opacity = 0);
  }});
}})();
</script>
</body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path
