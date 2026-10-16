"""Генератор синтетических данных в форме, близкой к Ozon Seller API (2026).

ВАЖНО. Данные полностью синтетические: товары, цены, объёмы и события
не относятся ни к одной реальной компании. Форма начислений повторяет
публичное описание метода /v1/finance/accrual/by-day (accrual_id, date,
unit_number, accrued_category, total_amount, вложенные узлы с type_id и
accrued), но точные имена вложенных полей — допущение, которое нужно
сверить на живом кабинете.

В данные намеренно заложены «истории», которые пайплайн должен найти сам,
без подсказок. Список — в SCENARIOS, проверка — в tests/.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

START = date(2026, 6, 1)
END = date(2026, 9, 30)
CLUSTERS = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Краснодар", "Новосибирск"]
CLUSTER_SHARE = [0.38, 0.14, 0.12, 0.12, 0.13, 0.11]
HUB = "Москва"
LOSS_DATE = date(2026, 8, 24)            # утрата остатка в кластере «Краснодар»
LOSS_CLUSTER = "Краснодар"
CLUSTER_CLOSED_UNTIL = date(2026, 9, 14)  # приёмка в кластере закрыта до этой даты
STORAGE_RUB_PER_L_DAY = 0.5               # параметр, сверить с актуальной таблицей Ozon
FREE_STORAGE_DAYS = 21
ACQUIRING_RATE = 0.015
LAST_MILE_RUB = 25.0
REVIEW_FEE_RUB = 50.0
POINTS_MAX_SHARE = 0.99
COMPENSATION_COEF = 0.41                  # коэффициент возмещения (FBO, > 300 ₽), публичный пример
VAT_RATE_IN_CARD = 0.22

SEASON = {
    "Пароочистители": {6: 0.90, 7: 0.85, 8: 0.95, 9: 1.10},
    "Моющие пылесосы": {6: 1.00, 7: 0.95, 8: 1.00, 9: 1.10},
    "Садовая техника": {6: 1.30, 7: 1.15, 8: 0.90, 9: 0.50},
    "ТВ-приставки": {6: 0.90, 7: 0.90, 8: 1.00, 9: 1.15},
}

SCENARIOS = {
    "S1": "SKU 1005: скидки Ozon отключены — баллов нет, вознаграждение по номиналу 51%, товар убыточен",
    "S2": "SKU 1004: премиальная модель с малой глубиной скидки Ozon — баллы покрывают малую часть тарифа",
    "S3": "SKU 1002: вторая карточка той же модели (SC-01) не пополняется с 10.07 — OOS при наличии товара на ФФ",
    "S4": "Садовая техника (3001-3003): большая закупка в августе на падающий сезон — затоваривание и хранение",
    "S5": "SKU 3001: высокий брак аккумуляторов — возвраты с утилизацией съедают маржу",
    "S6": "SKU 2002: автоподключённый «Сбор отзывов» + возвраты после отзыва",
    "S7": "24.08: утрата остатка в кластере «Краснодар»; возмещение начислено только по двум SKU",
    "S8": "Поставка SKU 1001 в «Казань» 07.09: принято на 12 шт. меньше, чем отгружено",
    "S9": "Отчёт о реализации расходится с API: 2001 (август) и 1003 (сентябрь)",
    "S10": "Performance API без данных за 14-15.09 — расходы на рекламу не сходятся с финансами",
    "S11": "С 01.09 появился новый тип начисления (type_id 999) без маппинга",
    "S12": "Штраф 18.08 пришёл только суммой total_amount, без детализации услуг",
    "S13": "ТВ-приставка 4/32 (TV-01): к росту спроса в Q4 закупили мало — риск OOS до следующей закупки",
}


@dataclass(frozen=True)
class SkuSim:
    sku: int
    offer_id: str
    model_id: str
    category: str
    nominal_rate: float
    volume_l: float
    landed_cost: float
    seller_price: float
    buyer_price: float
    base_demand: float      # заказов в день при сезонности 1.0
    buyout: float           # доля выкупа
    ret_rate: float         # доля возвратов после выкупа
    defect_share: float     # доля брака среди возвратов (списывается)
    drr: float              # целевой ДРР
    active_from: date


SKUS = [
    SkuSim(1002, "SC-01-B", "SC-01", "Пароочистители", .51, 18, 1350, 6500, 2850, 10, .93, .04, .4, .07, START),
    SkuSim(1001, "SC-01-A", "SC-01", "Пароочистители", .51, 18, 1350, 6500, 2850, 26, .93, .04, .4, .07, START),
    SkuSim(1003, "SC-02", "SC-02", "Пароочистители", .51, 9, 980, 4200, 2400, 22, .93, .05, .4, .08, START),
    SkuSim(1004, "SC-03", "SC-03", "Пароочистители", .51, 32, 5200, 16000, 12400, 4, .95, .03, .5, .06, START),
    SkuSim(1005, "SC-04", "SC-04", "Пароочистители", .51, 6, 1250, 3100, 3100, 18, .92, .05, .5, .09, START),
    SkuSim(2001, "WV-01", "WV-01", "Моющие пылесосы", .51, 22, 2050, 9000, 4300, 16, .94, .04, .4, .07, START),
    SkuSim(2002, "WV-02", "WV-02", "Моющие пылесосы", .51, 14, 1600, 7000, 3600, 12, .90, .06, .2, .10, date(2026, 8, 1)),
    SkuSim(3001, "BL-01", "BL-01", "Садовая техника", .52, 12, 1500, 12000, 2900, 14, .90, .11, .7, .07, START),
    SkuSim(3002, "BL-02", "BL-02", "Садовая техника", .52, 6, 700, 6000, 1700, 16, .91, .06, .5, .06, START),
    SkuSim(3003, "TR-01", "TR-01", "Садовая техника", .52, 15, 1400, 10000, 2600, 9, .90, .07, .5, .07, START),
    SkuSim(4001, "TV-01", "TV-01", "ТВ-приставки", .47, 2, 780, 5000, 2100, 30, .95, .03, .5, .05, START),
    SkuSim(4002, "TV-02", "TV-02", "ТВ-приставки", .47, 2, 1550, 7500, 3500, 10, .95, .03, .5, .08, date(2026, 8, 15)),
]
REVIEW_PROGRAM_SKUS = {2002, 4002}


def logistics_tariff(volume_l: float, local: bool) -> float:
    """Иллюстративный тариф логистики по объёму и маршруту (не официальная таблица)."""
    base = 50.0 + 8.0 * volume_l
    return round(base * (1.0 if local else 1.35), 2)


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def month_end(d: date) -> date:
    nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


class Generator:
    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)
        self.accruals: dict[date, list[dict]] = defaultdict(list)
        self.next_accrual_id = 9_000_000_000
        self.next_order = 10_000_000
        self.postings: list[dict] = []
        self.returns: list[dict] = []
        self.stock = {(s.sku, c): 0 for s in SKUS for c in CLUSTERS}
        self.ff = defaultdict(int)  # model_id -> qty
        self.events = defaultdict(list)  # date -> [(kind, payload)]
        self.accepted_log = defaultdict(list)  # (sku, cluster) -> [(date, qty)]
        self.in_transit = defaultdict(int)
        self.supplies: list[dict] = []
        self.stock_rows: list[dict] = []
        self.ff_rows: list[dict] = []
        self.ads_rows: list[dict] = []
        self.truth_lost_sales = defaultdict(int)
        self.truth_losses: dict[int, int] = {}
        self.month_pts = defaultdict(float)       # (sku, ym) -> points earned
        self.month_cov = defaultdict(float)       # (sku, ym) -> coverable services (positive)
        self.realization = defaultdict(lambda: {"qty_sold": 0, "amount_sold": 0.0,
                                                "qty_returned": 0, "amount_returned": 0.0})
        self.purchases: list[dict] = []
        self.sku_by_id = {s.sku: s for s in SKUS}

    # ---------- helpers ----------
    def aid(self) -> int:
        self.next_accrual_id += 1
        return self.next_accrual_id

    def add_posting_accrual(self, d: date, posting_number: str, sku: int, items: list[tuple[int, float]],
                            services: list[tuple[int, float]]):
        if d > END:
            return
        total = round(sum(a for _, a in items) + sum(a for _, a in services), 2)
        self.accruals[d].append({
            "accrual_id": self.aid(), "date": d.isoformat(), "unit_number": posting_number,
            "accrued_category": "POSTING",
            "total_amount": {"amount": total, "currency_code": "RUB"},
            "posting": {"products": [{
                "sku": sku, "quantity": 1,
                "accruals": [{"type_id": t, "accrued": {"amount": round(a, 2)}} for t, a in items],
                "delivery": {"services": [{"type_id": t, "accrued": {"amount": round(a, 2)}} for t, a in services]},
            }]},
        })

    def add_item_accrual(self, d: date, sku: int, type_id: int, amount: float, unit_number: str = ""):
        if d > END or abs(amount) < 0.005:
            return
        self.accruals[d].append({
            "accrual_id": self.aid(), "date": d.isoformat(), "unit_number": unit_number,
            "accrued_category": "ITEM",
            "total_amount": {"amount": round(amount, 2), "currency_code": "RUB"},
            "item": {"sku": sku},
            "item_fees": {"fees": [{"fees": [{"type_id": type_id, "accrued": {"amount": round(amount, 2)}}]}]},
        })

    def add_non_item(self, d: date, type_id: int | None, amount: float, detailed: bool = True):
        if d > END:
            return
        rec = {"accrual_id": self.aid(), "date": d.isoformat(), "unit_number": "",
               "accrued_category": "NON_ITEM",
               "total_amount": {"amount": round(amount, 2), "currency_code": "RUB"}}
        if detailed and type_id is not None:
            rec["non_item_fee"] = {"type_id": type_id, "accrued": {"amount": round(amount, 2)}}
        self.accruals[d].append(rec)

    def lam(self, s: SkuSim, d: date) -> float:
        if d < s.active_from:
            return 0.0
        return s.base_demand * SEASON[s.category][d.month]

    # ---------- setup ----------
    def setup(self):
        for s in SKUS:
            if s.active_from > START:
                continue
            for c, sh in zip(CLUSTERS, CLUSTER_SHARE):
                q = int(round(s.base_demand * sh * 28))
                if s.sku == 1002:
                    q = int(round(s.base_demand * sh * 25))
                self.stock[(s.sku, c)] = q
                self.accepted_log[(s.sku, c)].append((START - timedelta(days=30), q))
        models = defaultdict(float)
        for s in SKUS:
            models[s.model_id] += s.base_demand
        for m, dem in models.items():
            garden = m.startswith(("BL", "TR"))
            self.ff[m] = int(dem * (90 if garden else 40))
        # закупки (поступления на ФФ)
        po = 1
        for m, dem in models.items():
            s0 = next(x for x in SKUS if x.model_id == m)
            garden = m.startswith(("BL", "TR"))
            if garden:
                plan = [(date(2026, 6, 10), 40), (date(2026, 8, 15), 120)]   # S4: закупка на сезон по весенним продажам
            elif m == "WV-02":
                plan = [(date(2026, 7, 25), 60), (date(2026, 9, 20), 45)]
            elif m == "TV-01":                                                  # S13: к Q4 заказали мало
                plan = [(date(2026, 6, 20), 45), (date(2026, 8, 5), 40), (date(2026, 9, 20), 15)]
            elif m == "TV-02":
                plan = [(date(2026, 8, 10), 60), (date(2026, 9, 25), 45)]
            else:
                plan = [(date(2026, 6, 20), 45), (date(2026, 8, 5), 45), (date(2026, 9, 20), 45)]
            for eta, days in plan:
                qty = int(dem * days)
                self.purchases.append({"po_id": f"PO-{po:04d}", "model_id": m, "eta_date": eta.isoformat(),
                                       "qty": qty, "landed_cost_rub_unit": s0.landed_cost})
                self.events[eta].append(("ff_in", (m, qty)))
                po += 1

    # ---------- day loop ----------
    def ship_supplies(self, d: date):
        sup_n = len(self.supplies)
        for s in SKUS:
            if d < s.active_from - timedelta(days=5):
                continue
            if s.sku == 1002 and d > date(2026, 7, 10):     # S3: карточку B перестали пополнять
                continue
            for c, sh in zip(CLUSTERS, CLUSTER_SHARE):
                if c == LOSS_CLUSTER and LOSS_DATE <= d < CLUSTER_CLOSED_UNTIL:
                    continue
                lam_c = max(self.lam(s, d), s.base_demand if d < s.active_from else 0) * sh
                target = math.ceil(lam_c * 21) - self.stock[(s.sku, c)] - self.in_transit[(s.sku, c)]
                qty = min(max(target, 0), self.ff[s.model_id])
                if qty <= 0:
                    continue
                self.ff[s.model_id] -= qty
                self.in_transit[(s.sku, c)] += qty
                sup_n += 1
                acc_date = d + timedelta(days=4)
                accepted = qty
                if s.sku == 1001 and c == "Казань" and d == date(2026, 9, 7):   # S8
                    accepted = qty - 12
                self.supplies.append({"supply_id": f"SUP-{sup_n:05d}", "sku": s.sku, "cluster": c,
                                      "shipped_date": d.isoformat(), "shipped_qty": qty,
                                      "accepted_date": acc_date.isoformat(), "accepted_qty": accepted})
                self.events[acc_date].append(("accept", (s.sku, c, qty, accepted)))

    def sell(self, d: date):
        carry = 0
        for s in SKUS:
            lam = self.lam(s, d)
            if lam <= 0:
                continue
            if s.sku == 1001:
                lam_extra = carry
            else:
                lam_extra = 0
            for c, sh in zip(CLUSTERS, CLUSTER_SHARE):
                n = int(self.rng.poisson(lam * sh)) + (lam_extra if c == HUB else 0)
                for _ in range(n):
                    origin = None
                    if self.stock[(s.sku, c)] > 0:
                        origin = c
                    elif self.stock[(s.sku, HUB)] > 0:
                        origin = HUB
                    if origin is None:
                        self.truth_lost_sales[(d.isoformat(), s.sku)] += 1
                        if s.sku == 1002 and self.rng.random() < 0.4:
                            carry += 1   # часть спроса уходит на карточку A той же модели
                        continue
                    self.create_posting(s, d, origin, c)

    def create_posting(self, s: SkuSim, d: date, origin: str, dest: str):
        self.next_order += 1
        pn = f"{self.next_order:08d}-{s.sku % 10000:04d}-1"
        local = origin == dest
        logi = logistics_tariff(s.volume_l, local)
        row = {"posting_number": pn, "sku": s.sku, "offer_id": s.offer_id, "cluster_from": origin,
               "cluster_to": dest, "created_date": d.isoformat(), "status": "", "delivered_date": "",
               "outcome_date": "", "seller_price": s.seller_price, "buyer_price": s.buyer_price,
               "is_local": int(local), "return_reason": ""}
        if self.rng.random() < 0.02:
            row["status"] = "cancelled"
            self.postings.append(row)
            return
        self.stock[(s.sku, origin)] -= 1
        delivered = d + timedelta(days=int(self.rng.integers(1, 5)))
        row["delivered_date"] = delivered.isoformat()
        if delivered > END:
            row["status"] = "delivering"
            self.postings.append(row)
            return
        ym = delivered.strftime("%Y-%m")
        bought = self.rng.random() < s.buyout
        if not bought:
            nb = delivered + timedelta(days=1)
            if nb > END:
                row["status"] = "delivering"
                self.postings.append(row)
                return
            row["status"], row["outcome_date"] = "not_bought", nb.isoformat()
            self.add_posting_accrual(nb, pn, s.sku, [], [(3, -logi), (4, -LAST_MILE_RUB), (6, -logi)])
            self.month_cov[(s.sku, nb.strftime("%Y-%m"))] += 2 * logi + LAST_MILE_RUB
            rd = nb + timedelta(days=6)
            self.events[rd].append(("restock", (s.sku, origin, 1)))
            self.returns.append({"posting_number": pn, "sku": s.sku, "cluster": origin, "kind": "not_bought",
                                 "restock_date": rd.isoformat(), "qty": 1, "is_defect": 0})
            self.postings.append(row)
            return
        commission = round(s.nominal_rate * s.seller_price, 2)
        self.add_posting_accrual(delivered, pn, s.sku, [(1, s.buyer_price), (2, -commission)],
                                 [(3, -logi), (4, -LAST_MILE_RUB)])
        self.add_item_accrual(delivered, s.sku, 7, -ACQUIRING_RATE * s.buyer_price, pn)
        self.month_pts[(s.sku, ym)] += s.seller_price - s.buyer_price
        self.month_cov[(s.sku, ym)] += commission + logi + LAST_MILE_RUB
        rz = self.realization[(ym, s.sku)]
        rz["qty_sold"] += 1
        rz["amount_sold"] += s.buyer_price
        reviewed = s.sku in REVIEW_PROGRAM_SKUS and self.rng.random() < 0.15
        if reviewed:
            self.add_item_accrual(delivered + timedelta(days=2), s.sku, 10, -REVIEW_FEE_RUB, pn)
        ret_p = 0.35 if (reviewed and s.sku == 2002) else s.ret_rate
        if self.rng.random() < ret_p:
            rdate = delivered + timedelta(days=int(self.rng.integers(3, 15)))
            if rdate <= END:
                defect = self.rng.random() < s.defect_share
                row["status"], row["outcome_date"] = "returned", rdate.isoformat()
                row["return_reason"] = "defect" if defect else "changed_mind"
                self.add_posting_accrual(rdate, pn, s.sku, [(14, -s.buyer_price), (2, commission)], [(6, -logi)])
                self.add_item_accrual(rdate, s.sku, 7, ACQUIRING_RATE * s.buyer_price, pn)
                rym = rdate.strftime("%Y-%m")
                self.month_pts[(s.sku, rym)] -= s.seller_price - s.buyer_price
                self.month_cov[(s.sku, rym)] += -commission + logi
                rr = self.realization[(rym, s.sku)]
                rr["qty_returned"] += 1
                rr["amount_returned"] += s.buyer_price
                restock = rdate + timedelta(days=5)
                if not defect:
                    self.events[restock].append(("restock", (s.sku, origin, 1)))
                self.returns.append({"posting_number": pn, "sku": s.sku, "cluster": origin, "kind": "return",
                                     "restock_date": restock.isoformat() if not defect else "",
                                     "qty": 1, "is_defect": int(defect)})
                self.postings.append(row)
                return
        row["status"] = "delivered"
        self.postings.append(row)

    def storage(self, d: date):
        for s in SKUS:
            fee = 0.0
            for c in CLUSTERS:
                free = sum(q for dd, q in self.accepted_log[(s.sku, c)] if (d - dd).days < FREE_STORAGE_DAYS)
                charge_units = max(0, self.stock[(s.sku, c)] - free)
                fee += charge_units * s.volume_l * STORAGE_RUB_PER_L_DAY
            self.add_item_accrual(d, s.sku, 8, -fee)

    def ads(self, d: date):
        total = 0.0
        for s in SKUS:
            lam = self.lam(s, d)
            if lam <= 0:
                continue
            spend = round(s.drr * lam * s.buyer_price * float(self.rng.lognormal(0, 0.15)), 2)
            total += spend
            if not (date(2026, 9, 14) <= d <= date(2026, 9, 15)):   # S10: дыра в выгрузке Performance API
                self.ads_rows.append({"date": d.isoformat(), "sku": s.sku, "spend": spend})
        self.add_non_item(d, 9, -total)

    def month_close(self, d: date):
        ym = d.strftime("%Y-%m")
        for s in SKUS:
            pts = max(self.month_pts.get((s.sku, ym), 0.0), 0.0)
            cov = max(self.month_cov.get((s.sku, ym), 0.0), 0.0) * POINTS_MAX_SHARE
            used = round(min(pts, cov), 2)
            self.add_item_accrual(d, s.sku, 13, used)

    def run(self):
        self.setup()
        for d in daterange(START, END):
            for kind, payload in self.events.pop(d, []):
                if kind == "ff_in":
                    m, q = payload
                    self.ff[m] += q
                elif kind == "accept":
                    sku, c, shipped, accepted = payload
                    self.in_transit[(sku, c)] -= shipped
                    self.stock[(sku, c)] += accepted
                    self.accepted_log[(sku, c)].append((d, accepted))
                elif kind == "restock":
                    sku, c, q = payload
                    self.stock[(sku, c)] += q
            if d == LOSS_DATE:                                     # S7
                for s in SKUS:
                    q = self.stock[(s.sku, LOSS_CLUSTER)]
                    if q > 0:
                        self.truth_losses[s.sku] = q
                        self.stock[(s.sku, LOSS_CLUSTER)] = 0
            if d.weekday() == 0:
                self.ship_supplies(d)
            self.sell(d)
            self.storage(d)
            self.ads(d)
            for s in SKUS:
                for c in CLUSTERS:
                    self.stock_rows.append({"date": d.isoformat(), "sku": s.sku, "cluster": c,
                                            "qty": self.stock[(s.sku, c)]})
            for m, q in sorted(self.ff.items()):
                self.ff_rows.append({"date": d.isoformat(), "model_id": m, "qty": q})
            if d == month_end(d):
                self.month_close(d)
        # штрафы и прочее
        self.add_non_item(date(2026, 7, 15), 11, -1500)
        self.add_non_item(date(2026, 8, 18), 11, -3000, detailed=False)  # S12: только total_amount
        self.add_non_item(date(2026, 9, 18), 11, -2000)
        for d in daterange(date(2026, 9, 1), END):                          # S11
            self.add_non_item(d, 999, -150)
        # возмещение за утрату — только по двум SKU (S7)
        for sku in (1001, 2001):
            if sku in self.truth_losses:
                s = self.sku_by_id[sku]
                amt = self.truth_losses[sku] * s.buyer_price * COMPENSATION_COEF / (1 + VAT_RATE_IN_CARD)
                self.add_item_accrual(date(2026, 9, 25), sku, 12, amt)

    # ---------- export ----------
    def export(self, out: Path):
        raw = out / "raw" / "accruals"
        raw.mkdir(parents=True, exist_ok=True)
        for d in daterange(START, END):
            items = sorted(self.accruals.get(d, []), key=lambda a: a["accrual_id"])
            (raw / f"{d.isoformat()}.json").write_text(
                json.dumps({"date": d.isoformat(), "accruals": items, "last_id": ""}, ensure_ascii=False),
                encoding="utf-8")
        types = [{"type_id": 1, "name": "Начислено за товар"}, {"type_id": 2, "name": "Вознаграждение за продажу"},
                 {"type_id": 3, "name": "Логистика"}, {"type_id": 4, "name": "Доставка до места выдачи"},
                 {"type_id": 6, "name": "Обратная логистика"}, {"type_id": 7, "name": "Приём платежа"},
                 {"type_id": 8, "name": "Размещение товаров"}, {"type_id": 9, "name": "Продвижение: оплата за клик"},
                 {"type_id": 10, "name": "Сбор отзывов"}, {"type_id": 11, "name": "Штраф"},
                 {"type_id": 12, "name": "Возмещение за утраченный товар"},
                 {"type_id": 13, "name": "Списание баллов за скидки в счёт услуг"},
                 {"type_id": 14, "name": "Возврат выручки"}, {"type_id": 999, "name": "Новая услуга"}]
        (out / "raw" / "accrual_types.json").write_text(json.dumps({"types": types}, ensure_ascii=False), "utf-8")

        def write(name, rows, fields=None):
            path = out / name
            path.parent.mkdir(parents=True, exist_ok=True)
            fields = fields or list(rows[0].keys())
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)

        write("raw/postings.csv", self.postings)
        write("raw/returns.csv", self.returns)
        write("raw/stocks_ozon.csv", self.stock_rows)
        write("raw/supplies.csv", self.supplies)
        write("raw/performance_ads.csv", self.ads_rows)
        write("external/ff_stocks.csv", self.ff_rows)
        write("external/purchases.csv", self.purchases)
        rz_rows = []
        for (ym, sku), v in sorted(self.realization.items()):
            r = {"month": ym, "sku": sku, **{k: round(x, 2) if isinstance(x, float) else x for k, x in v.items()}}
            if (ym, sku) == ("2026-08", 2001):                                  # S9
                r["qty_sold"] += 2
                r["amount_sold"] = round(r["amount_sold"] + 2 * 4300, 2)
            if (ym, sku) == ("2026-09", 1003):
                r["qty_returned"] -= 1
                r["amount_returned"] = round(r["amount_returned"] - 2400, 2)
            rz_rows.append(r)
        write("external/realization_report.csv", rz_rows)
        write("truth/losses.csv", [{"sku": k, "qty": v} for k, v in sorted(self.truth_losses.items())])
        write("truth/lost_sales.csv", [{"date": d, "sku": k, "qty": v}
                                       for (d, k), v in sorted(self.truth_lost_sales.items())],
              ["date", "sku", "qty"])
        (out / "truth" / "scenarios.json").write_text(json.dumps(SCENARIOS, ensure_ascii=False, indent=2), "utf-8")


def generate(out_dir: str | Path = "data/demo", seed: int = 42) -> Path:
    out = Path(out_dir)
    g = Generator(seed)
    g.run()
    g.export(out)
    return out


if __name__ == "__main__":
    p = generate()
    print(f"Синтетические данные записаны в {p}")
