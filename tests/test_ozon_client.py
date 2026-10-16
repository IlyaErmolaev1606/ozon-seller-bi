from datetime import date

import pytest

from osbi.ozon_client import OzonApiError, OzonCredentials, OzonSellerClient


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


def make_client(responses):
    c = OzonSellerClient(OzonCredentials("1", "k"), min_interval=0)
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)
        return responses.pop(0)

    c.session.post = fake_post
    return c, calls


def test_accruals_paginate_by_cursor_one_day():
    c, calls = make_client([FakeResp(200, {"accruals": [{"accrual_id": 1}], "last_id": "a"}),
                            FakeResp(200, {"accruals": [{"accrual_id": 2}], "last_id": ""})])
    rows = list(c.accruals_by_day(date(2026, 9, 15)))
    assert [r["accrual_id"] for r in rows] == [1, 2]
    assert calls[0] == {"date": "2026-09-15"} and calls[1] == {"date": "2026-09-15", "last_id": "a"}


def test_retries_on_429(monkeypatch):
    monkeypatch.setattr("osbi.ozon_client.time.sleep", lambda s: None)
    c, _ = make_client([FakeResp(429), FakeResp(200, {"accruals": [], "last_id": ""})])
    assert list(c.accruals_by_day(date(2026, 9, 15))) == []


def test_obsolete_method_fails_loudly():
    c, _ = make_client([FakeResp(400, text='{"code": 9, "message": "obsolete method cannot be used"}')])
    with pytest.raises(OzonApiError, match="obsolete"):
        list(c.accruals_by_day(date(2026, 9, 15)))


def test_key_expiry_days_left():
    creds = OzonCredentials("1", "k", key_expires=date(2026, 12, 2))
    assert creds.days_left(today=date(2026, 11, 20)) == 12
