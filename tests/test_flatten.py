import json

from osbi.flatten import flatten_accrual, flatten_dir


def test_components_and_sku_from_product():
    acc = {"accrual_id": 1, "date": "2026-09-15", "unit_number": "P-1", "accrued_category": "POSTING",
           "total_amount": {"amount": 306.5},
           "posting": {"products": [{"sku": 1001, "accruals": [
               {"type_id": 1, "accrued": {"amount": 790}}, {"type_id": 2, "accrued": {"amount": -379.2}}],
               "delivery": {"services": [{"type_id": 3, "accrued": {"amount": -104.3}}]}}]}}
    rows = flatten_accrual(acc)
    assert {r["type_id"] for r in rows} == {1, 2, 3}
    assert all(r["sku"] == 1001 for r in rows)
    assert not any(r["is_remainder"] for r in rows)
    assert round(sum(r["amount"] for r in rows), 2) == 306.5


def test_sku_in_sibling_branch_is_propagated():
    """В ITEM-начислениях SKU лежит в item.sku, а сумма — в item_fees: SKU не должен потеряться."""
    acc = {"accrual_id": 2, "date": "2026-09-15", "unit_number": "", "accrued_category": "ITEM",
           "total_amount": {"amount": -42.0}, "item": {"sku": 2001},
           "item_fees": {"fees": [{"fees": [{"type_id": 8, "accrued": {"amount": -42.0}}]}]}}
    rows = flatten_accrual(acc)
    assert rows[0]["sku"] == 2001


def test_total_only_record_becomes_remainder():
    """Штраф пришёл только итогом — сумма не теряется, а помечается как нераспределённая."""
    acc = {"accrual_id": 3, "date": "2026-08-18", "unit_number": "", "accrued_category": "NON_ITEM",
           "total_amount": {"amount": -3000}}
    rows = flatten_accrual(acc)
    assert len(rows) == 1 and rows[0]["is_remainder"] and rows[0]["amount"] == -3000


def test_multi_sku_posting_does_not_guess_sku_for_shared_lines():
    acc = {"accrual_id": 4, "date": "2026-09-15", "unit_number": "P-4", "accrued_category": "POSTING",
           "total_amount": {"amount": 100},
           "posting": {"products": [{"sku": 1, "accruals": [{"type_id": 1, "accrued": {"amount": 60}}]},
                                    {"sku": 2, "accruals": [{"type_id": 1, "accrued": {"amount": 60}}]}]},
           "container_fees": [{"type_id": 5, "accrued": {"amount": -20}}]}
    rows = flatten_accrual(acc)
    shared = [r for r in rows if r["type_id"] == 5]
    assert shared[0]["sku"] is None


def test_reloading_same_day_is_idempotent(tmp_path):
    acc = {"accrual_id": 5, "date": "2026-09-15", "unit_number": "", "accrued_category": "NON_ITEM",
           "total_amount": {"amount": -10}, "non_item_fee": {"type_id": 9, "accrued": {"amount": -10}}}
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(json.dumps({"accruals": [acc]}), encoding="utf-8")
    assert len(flatten_dir(tmp_path)) == 1
