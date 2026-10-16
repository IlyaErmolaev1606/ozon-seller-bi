"""Интеграционные тесты: пайплайн сам находит ситуации, заложенные в синтетические данные."""
import csv
import json

import pytest


def q(con, sql, *params):
    return con.execute(sql, list(params)).fetchall()


def test_accrual_lines_sum_to_api_totals(built):
    con, data_dir = built
    total = 0.0
    for path in (data_dir / "raw" / "accruals").glob("*.json"):
        for a in json.loads(path.read_text(encoding="utf-8"))["accruals"]:
            total += a["total_amount"]["amount"]
    (lines,) = q(con, "select sum(amount) from stg_accrual_lines")[0]
    assert lines == pytest.approx(total, abs=0.05)


def test_s1_disabled_discounts_sku_is_loss_making(built):
    con, _ = built
    (pts, margin), = q(con, "select points_offset, margin_pct from mart_sku_month where sku=1005 and month='2026-09'")
    assert pts == 0 and margin < 0


def test_s2_premium_sku_has_high_effective_take_rate(built):
    con, _ = built
    (etr,), = q(con, "select effective_take_rate from mart_sku_month where sku=1004 and month='2026-09'")
    assert etr > 0.3


def test_s3_second_card_out_of_stock_while_ff_has_goods(built):
    con, _ = built
    (action,), = q(con, "select action from mart_stock_plan where sku=1002")
    assert "срочная поставка" in action
    (oos_days, lost), = q(con, "select oos_days, lost_units_est from mart_oos where sku=1002")
    assert oos_days == 30 and lost > 100


def test_s4_garden_overstock_flagged(built):
    con, _ = built
    rows = q(con, "select model_id from mart_purchase_plan where excess_units > 0")
    assert {r[0] for r in rows} >= {"BL-01", "BL-02", "TR-01"}


def test_s7_losses_match_truth_and_two_skus_compensated(built):
    con, data_dir = built
    with open(data_dir / "truth" / "losses.csv", encoding="utf-8") as f:
        truth = {int(r["sku"]): int(r["qty"]) for r in csv.DictReader(f)}
    found = dict(q(con, "select sku, lost_qty from mart_stock_losses"))
    assert {k: int(v) for k, v in found.items()} == truth
    compensated = {r[0] for r in q(con, "select sku from mart_stock_losses where status = 'Возмещено'")}
    assert compensated == {1001, 2001}


def test_s8_acceptance_shortage(built):
    con, _ = built
    rows = q(con, "select sku, cluster, diff_qty from mart_supply_acceptance where status <> 'OK'")
    assert rows == [(1001, "Казань", -12)]


def test_s9_realization_mismatches(built):
    con, _ = built
    rows = q(con, "select month, sku from mart_recon_realization where status <> 'OK' order by month")
    assert rows == [("2026-08", 2001), ("2026-09", 1003)]


def test_s10_ads_gap_detected(built):
    con, _ = built
    (status, days), = q(con, "select status, days_missing_in_performance from mart_recon_ads where month='2026-09'")
    assert status == "Расхождение" and days == "14.09, 15.09"


def test_s11_s12_data_quality_alerts(built):
    con, _ = built
    dq = dict(q(con, "select check_name, status from mart_dq"))
    assert dq["Новые типы начислений без маппинга"] == "Тревога"
    assert dq["Начисления без детализации (только total_amount)"] == "Проверить"
    assert dq["Выкупленные отправления без начисления выручки"] == "OK"


def test_s13_reorder_recommended_for_tv01(built):
    con, _ = built
    (qty,), = q(con, "select recommended_order_qty from mart_purchase_plan where model_id='TV-01'")
    assert qty > 0


def test_plan_model_calibrated_to_actuals(built):
    """Плановая модель на параметрах месяца воспроизводит фактическую маржу (±3 п.п.)."""
    con, _ = built
    rows = q(con, "select sku, margin_actual, margin_model from mart_scenarios")
    close = [r for r in rows if abs(r[1] - r[2]) <= 0.03]
    assert len(close) >= len(rows) - 2
