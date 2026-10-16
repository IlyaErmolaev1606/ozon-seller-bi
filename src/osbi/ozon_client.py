"""Клиент Ozon Seller API: ретраи, лимиты, пагинация, учёт срока жизни ключа.

Статус методов на октябрь 2026 (см. docs/06_ozon_api_notes_2026.md):
  * /v1/finance/accrual/by-day — замена отключённого /v3/finance/transaction/list;
    запрос строго за один день, пагинация курсором last_id. Описан в публичном
    разборе миграции (Хабр, 25.09.2026) — проверено на чужих боевых данных.
  * /v1/finance/accrual/postings — НЕ использовать для чистого дохода: отдаёт
    только POSTING-начисления, без ITEM (эквайринг), сумма меньше примерно на 1,2%.
  * остальные методы — по документации; перед запуском сверить тела запросов и
    поля ответа на dev.ozon.ru (Ozon часто меняет версии методов).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterator

import requests

log = logging.getLogger("osbi.ozon")

BASE_URL = "https://api-seller.ozon.ru"

ENDPOINTS = {
    "accrual_by_day": ("/v1/finance/accrual/by-day", "подтверждён (миграция 09.2026)"),
    "accrual_types": ("/v1/finance/accrual/types", "подтверждён (миграция 09.2026)"),
    "postings_fbo": ("/v2/posting/fbo/list", "сверить поля ответа"),
    "product_list": ("/v3/product/list", "сверить"),
    "product_stocks": ("/v4/product/info/stocks", "сверить"),
    "analytics_data": ("/v1/analytics/data", "сверить"),
}


class OzonApiError(RuntimeError):
    def __init__(self, status: int, message: str, path: str):
        super().__init__(f"{path}: HTTP {status}: {message}")
        self.status, self.path = status, path


@dataclass
class OzonCredentials:
    client_id: str
    api_key: str
    key_expires: date | None = None   # ключи живут ограниченное время — храним дату истечения

    @classmethod
    def from_env(cls) -> "OzonCredentials":
        exp = os.getenv("OZON_API_KEY_EXPIRES")
        return cls(client_id=os.environ["OZON_CLIENT_ID"], api_key=os.environ["OZON_API_KEY"],
                   key_expires=date.fromisoformat(exp) if exp else None)

    def days_left(self, today: date | None = None) -> int | None:
        if not self.key_expires:
            return None
        return (self.key_expires - (today or date.today())).days


class OzonSellerClient:
    def __init__(self, creds: OzonCredentials, base_url: str = BASE_URL, timeout: float = 60.0,
                 max_retries: int = 5, min_interval: float = 0.25):
        self.creds = creds
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update({"Client-Id": creds.client_id, "Api-Key": creds.api_key,
                                     "Content-Type": "application/json"})
        left = creds.days_left()
        if left is not None and left <= 14:
            log.warning("API-ключ Ozon истекает через %s дн. — выпустите новый и обновите секрет", left)

    # ---------- транспорт ----------
    def post(self, path: str, body: dict) -> dict:
        for attempt in range(self.max_retries):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            resp = self.session.post(self.base_url + path, json=body, timeout=self.timeout)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                backoff = min(2 ** attempt, 30)
                log.warning("%s: HTTP %s, повтор через %s с", path, resp.status_code, backoff)
                time.sleep(backoff)
                continue
            # 400 {"code": 9, "message": "obsolete method cannot be used"} — метод отключён: падаем громко
            raise OzonApiError(resp.status_code, resp.text[:500], path)
        raise OzonApiError(429, "превышено число повторов", path)

    # ---------- финансы ----------
    def accrual_types(self) -> list[dict]:
        data = self.post(ENDPOINTS["accrual_types"][0], {})
        return data.get("types") or data.get("result") or []

    def accruals_by_day(self, day: date, max_pages: int = 200) -> Iterator[dict]:
        """Все начисления за день. Предохранитель на число страниц — чтобы не уйти в бесконечный цикл."""
        last_id = ""
        for _ in range(max_pages):
            body = {"date": day.isoformat()}
            if last_id:
                body["last_id"] = last_id
            data = self.post(ENDPOINTS["accrual_by_day"][0], body)
            rows = data.get("accruals") or []
            yield from rows
            last_id = data.get("last_id") or ""
            if not last_id or not rows:
                return
        raise OzonApiError(-1, f"страниц за {day} больше {max_pages}", ENDPOINTS["accrual_by_day"][0])

    # ---------- заказы ----------
    def postings_fbo(self, since: datetime, to: datetime, page_size: int = 1000) -> Iterator[dict]:
        offset = 0
        while True:
            body = {"dir": "ASC", "filter": {"since": since.isoformat() + "Z", "to": to.isoformat() + "Z"},
                    "limit": page_size, "offset": offset,
                    "with": {"analytics_data": True, "financial_data": True}}
            data = self.post(ENDPOINTS["postings_fbo"][0], body)
            rows = data.get("result") or []
            yield from rows
            if len(rows) < page_size:
                return
            offset += page_size
