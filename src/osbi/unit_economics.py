"""Плановая юнит-экономика: ценовой калькулятор, оценка акций, сценарии тарифов.

Модель учитывает механику баллов Ozon (2026): номинальный тариф считается от
цены продавца, а на разницу между ценой продавца и ценой для покупателя
начисляются баллы, которыми в конце месяца гасится до 99% стоимости
вознаграждения и услуг. Если скидки Ozon отключены, баллов нет и тариф
платится по номиналу.

Какие именно услуги гасятся баллами и в каком порядке — по публичным
разъяснениям Ozon однозначно не описано; по умолчанию: вознаграждение,
логистика и доставка до места выдачи. Параметр coverable_* можно поменять,
когда механика будет подтверждена на закрытом месяце (УПД-1, «Экономика магазина»).
"""
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class UnitInputs:
    buyer_price: float                 # цена для покупателя (цена реализации)
    seller_price: float                # цена продавца — база номинального тарифа
    nominal_rate: float                # ставка вознаграждения категории (доля)
    logistics: float                   # тариф логистики на единицу
    landed_cost: float                 # себестоимость с доставкой партии и таможней
    last_mile: float = 25.0
    acquiring_rate: float = 0.015
    ff_cost: float = 0.0               # фулфилмент на единицу
    storage_per_unit: float = 0.0      # хранение, распределённое на проданную единицу
    other_fees_per_unit: float = 0.0   # прочие услуги Ozon на единицу (отзывы и т.п.), не гасятся баллами
    ads_rate: float = 0.0              # ДРР, доля выручки
    buyout_rate: float = 1.0           # доля выкупа доставленных заказов
    return_rate: float = 0.0           # доля возвратов среди выкупленных
    defect_share: float = 0.0          # доля брака среди возвратов (списание)
    ozon_discounts_enabled: bool = True
    points_max_share: float = 0.99
    vat_rate: float = 0.05             # НДС с продаж (УСН с НДС 5% по умолчанию)
    usn_rate: float = 0.06             # УСН «доходы»


def points_offset(seller_price: float, buyer_price: float, coverable_services: float,
                  enabled: bool = True, max_share: float = 0.99) -> dict:
    """Сколько услуг погасят баллы за скидку Ozon.

    Пример Ozon: цена продавца 1000, цена для покупателя 400, тариф 52% (520 ₽):
    баллов 600, гасится 99% тарифа -> оплачено деньгами 5,2 ₽.
    """
    points = max(seller_price - buyer_price, 0.0) if enabled else 0.0
    cap = max(coverable_services, 0.0) * max_share
    used = min(points, cap)
    return {"points": points, "used": used, "leftover": points - used,
            "paid_services": coverable_services - used}


def unit_economics(x: UnitInputs) -> dict:
    """Ожидаемая экономика на один выкупленный заказ (с учётом невыкупов и возвратов).

    Возврат: выручка, вознаграждение и баллы сторнируются, логистика не возвращается,
    добавляется обратная логистика; бракованная единица списывается.
    Невыкуп: логистика туда и обратно + доставка до места выдачи.
    """
    r = x.return_rate
    keep = 1.0 - r
    commission = x.nominal_rate * x.seller_price * keep
    failed_per_bought = (1 - x.buyout_rate) / x.buyout_rate if x.buyout_rate > 0 else 0.0
    failed_cost = failed_per_bought * (2 * x.logistics + x.last_mile)
    reverse_logistics = r * x.logistics
    coverable = commission + x.logistics + x.last_mile + reverse_logistics + failed_cost
    pts = points_offset(x.seller_price * keep, x.buyer_price * keep, coverable,
                        x.ozon_discounts_enabled, x.points_max_share)

    revenue = x.buyer_price * keep
    acquiring = x.acquiring_rate * x.buyer_price * keep
    ozon_fees = pts["paid_services"] + acquiring + x.storage_per_unit + x.other_fees_per_unit

    cogs = x.landed_cost * (keep + r * x.defect_share)        # проданное + списанный брак
    ff = x.ff_cost
    ads = x.ads_rate * revenue
    vat = revenue * x.vat_rate / (1 + x.vat_rate)
    income_tax = (revenue - vat) * x.usn_rate
    profit = revenue - ozon_fees - cogs - ff - ads - vat - income_tax
    return {
        "revenue": revenue,
        "nominal_commission": commission,
        "points": pts["points"],
        "points_used": pts["used"],
        "points_leftover": pts["leftover"],
        "ozon_fees": ozon_fees,
        "effective_take_rate": ozon_fees / revenue if revenue else float("nan"),
        "cogs": cogs, "ff": ff, "ads": ads, "vat": vat, "income_tax": income_tax,
        "profit": profit,
        "margin": profit / revenue if revenue else float("nan"),
    }


def promo_breakeven(x: UnitInputs, promo_discount: float) -> dict:
    """Во сколько раз должны вырасти продажи в акции, чтобы прибыль не упала.

    Акция продавца = снижение цены продавца на promo_discount (доля). Допущение:
    собственная скидка Ozon в рублях остаётся прежней, поэтому цена для покупателя
    падает на ту же сумму. Как Ozon пересчитывает свою скидку в акциях — проверить
    на истории акций кабинета (это первая задача калибровки калькулятора).
    """
    cut = x.seller_price * promo_discount
    promo_x = replace(x, seller_price=x.seller_price - cut, buyer_price=max(x.buyer_price - cut, 1.0))
    base, promo = unit_economics(x), unit_economics(promo_x)
    mult = float("inf") if promo["profit"] <= 0 else base["profit"] / promo["profit"]
    return {"profit_base": base["profit"], "profit_promo": promo["profit"],
            "required_volume_multiplier": mult, "promo_margin": promo["margin"],
            "promo_buyer_price": promo_x.buyer_price}


SCENARIOS = {
    "rate_plus_3pp": "Ставка вознаграждения +3 п.п.",
    "deeper_discount_10": "Ozon углубил скидку: цена для покупателя −10% при той же цене продавца",
    "points_cap_50": "Баллы гасят не 99%, а 50% услуг (изменение правил)",
}


def tariff_shock(x: UnitInputs, scenario: str) -> dict:
    """Прибыль на единицу при изменении правил площадки (объём продаж — неизменный).

    При механике баллов чистая выручка продавца ≈ цене для покупателя, а её
    задаёт скидка Ozon. Поэтому для «дисконтируемых» SKU главный риск —
    не рост ставки, а изменение глубины скидки и правил погашения баллами.
    Эластичность спроса здесь не моделируется: это верхняя оценка эффекта
    на единицу, объёмный эффект оценивается отдельно по факту.
    """
    if scenario == "rate_plus_3pp":
        shocked_x = replace(x, nominal_rate=x.nominal_rate + 0.03)
    elif scenario == "deeper_discount_10":
        shocked_x = replace(x, buyer_price=x.buyer_price * 0.90)
    elif scenario == "points_cap_50":
        shocked_x = replace(x, points_max_share=0.50)
    else:
        raise ValueError(f"Неизвестный сценарий: {scenario}")
    base, shocked = unit_economics(x), unit_economics(shocked_x)
    return {"profit_base": base["profit"], "profit_shock": shocked["profit"],
            "delta_profit": shocked["profit"] - base["profit"],
            "margin_base": base["margin"], "margin_shock": shocked["margin"]}
