"""Разворачивание начислений /v1/finance/accrual/by-day в плоские строки.

Принципы (выучены на смене API в 2026 году):
1. total_amount.amount — источник истины. Компоненты (type_id + accrued) могут
   не сходиться с итогом (штрафы, компенсации приходят только итогом),
   поэтому разница сохраняется отдельной строкой is_remainder = True,
   а не теряется и не «пересчитывается».
2. Форму дерева не угадываем: рекурсивно собираем любые узлы вида
   {"type_id": ..., "accrued": {"amount": ...}} на любой глубине и
   протаскиваем контекст SKU, если он встретился выше по дереву.
3. accrual_id стабилен (равен operation_id старого API) — по нему делаем
   идемпотентный upsert.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def _amount(node: Any) -> float:
    if isinstance(node, dict):
        return float(node.get("amount", 0) or 0)
    if node is None:
        return 0.0
    return float(node)


def _walk(node: Any, sku: int | None, out: list[tuple[int | None, int, float]]) -> None:
    if isinstance(node, dict):
        if "sku" in node and isinstance(node["sku"], (int, str)) and str(node["sku"]).isdigit():
            sku = int(node["sku"])
        if "type_id" in node and isinstance(node.get("accrued"), dict):
            out.append((sku, int(node["type_id"]), _amount(node["accrued"])))
            return
        for key, value in node.items():
            if key == "total_amount":
                continue
            _walk(value, sku, out)
    elif isinstance(node, list):
        for value in node:
            _walk(value, sku, out)


def _collect_skus(node: Any, out: set[int]) -> None:
    if isinstance(node, dict):
        if "sku" in node and str(node["sku"]).isdigit():
            out.add(int(node["sku"]))
        for value in node.values():
            _collect_skus(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_skus(value, out)


def flatten_accrual(acc: dict) -> list[dict]:
    """Одна запись API -> список строк (компоненты + остаток до total_amount)."""
    parts: list[tuple[int | None, int, float]] = []
    _walk(acc, None, parts)
    all_skus: set[int] = set()
    _collect_skus(acc, all_skus)   # SKU может лежать в соседней ветке (item.sku рядом с item_fees)
    total = round(_amount(acc.get("total_amount")), 2)
    base = {
        "accrual_id": int(acc["accrual_id"]),
        "date": acc["date"],
        "category": acc.get("accrued_category", ""),
        "unit_number": acc.get("unit_number", "") or "",
    }
    # Если в записи ровно один SKU — строки без явного SKU относим к нему.
    # Если SKU несколько (многотоварное отправление) — строки без SKU остаются
    # нераспределёнными и распределяются в витрине по правилу, а не угадываются здесь.
    default_sku = next(iter(all_skus)) if len(all_skus) == 1 else None
    rows = []
    for sku, type_id, amount in parts:
        rows.append({**base, "sku": sku if sku is not None else default_sku, "type_id": type_id,
                     "amount": round(amount, 2), "is_remainder": False})
    remainder = round(total - sum(r["amount"] for r in rows), 2)
    if abs(remainder) >= 0.01:
        rows.append({**base, "sku": default_sku, "type_id": None, "amount": remainder, "is_remainder": True})
    return rows


def iter_accrual_files(raw_dir: str | Path) -> Iterator[dict]:
    for path in sorted(Path(raw_dir).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for acc in payload.get("accruals", []):
            yield acc


def flatten_dir(raw_dir: str | Path) -> list[dict]:
    rows: list[dict] = []
    seen: set[int] = set()
    for acc in iter_accrual_files(raw_dir):
        aid = int(acc["accrual_id"])
        if aid in seen:          # идемпотентность при повторной загрузке дня
            continue
        seen.add(aid)
        rows.extend(flatten_accrual(acc))
    return rows
