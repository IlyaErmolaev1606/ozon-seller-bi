import math

import pytest

from osbi.unit_economics import UnitInputs, points_offset, promo_breakeven, tariff_shock, unit_economics


def test_ozon_public_example_points():
    """Пример Ozon: цена продавца 1000, для покупателя 400, тариф 52% -> деньгами платится 5,2 ₽."""
    r = points_offset(seller_price=1000, buyer_price=400, coverable_services=520)
    assert r["points"] == 600
    assert r["used"] == pytest.approx(514.8)
    assert r["paid_services"] == pytest.approx(5.2)
    # В разборе SelSup остаток баллов указан 74,8; по логике «до 99% услуг» он 85,2.
    # Расхождение фиксируем тестом и проверяем на первом закрытом месяце реального кабинета.
    assert r["leftover"] == pytest.approx(85.2)


def test_points_disabled_pays_nominal():
    r = points_offset(1000, 400, 520, enabled=False)
    assert r["used"] == 0 and r["paid_services"] == 520


def _base(**kw):
    x = dict(buyer_price=3100, seller_price=3100, nominal_rate=0.51, logistics=98, landed_cost=1250,
             ff_cost=45, ads_rate=0.09, buyout_rate=0.92, return_rate=0.05, defect_share=0.5)
    x.update(kw)
    return UnitInputs(**x)


def test_disabled_discounts_make_sku_unprofitable():
    off = unit_economics(_base(ozon_discounts_enabled=False))
    assert off["profit"] < 0
    assert off["effective_take_rate"] > 0.5


def test_ozon_discount_turns_same_product_profitable():
    on = unit_economics(_base(seller_price=6000, buyer_price=3100))
    off = unit_economics(_base(ozon_discounts_enabled=False))
    assert on["profit"] > 0
    assert on["effective_take_rate"] < 0.15 < off["effective_take_rate"]


def test_returns_reduce_profit_monotonically():
    low = unit_economics(_base(seller_price=6000, return_rate=0.02))["profit"]
    high = unit_economics(_base(seller_price=6000, return_rate=0.15))["profit"]
    assert high < low


def test_promo_breakeven_requires_more_volume():
    x = _base(seller_price=6000)
    r = promo_breakeven(x, promo_discount=0.10)
    assert r["required_volume_multiplier"] > 1


def test_promo_breakeven_infinite_when_promo_loses_money():
    r = promo_breakeven(_base(ozon_discounts_enabled=False), promo_discount=0.15)
    assert math.isinf(r["required_volume_multiplier"])


@pytest.mark.parametrize("scenario", ["rate_plus_3pp", "deeper_discount_10", "points_cap_50"])
def test_tariff_shock_runs(scenario):
    r = tariff_shock(_base(seller_price=6000), scenario)
    assert set(r) >= {"profit_base", "profit_shock", "delta_profit"}


def test_points_cap_hurts_discounted_sku_more_than_rate_hike():
    x = _base(seller_price=6000)
    assert tariff_shock(x, "points_cap_50")["delta_profit"] < tariff_shock(x, "rate_plus_3pp")["delta_profit"]
