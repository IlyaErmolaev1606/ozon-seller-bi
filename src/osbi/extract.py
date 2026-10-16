"""Выгрузка из Ozon Seller API в сырой слой (raw) — идемпотентно, по дням.

    export OZON_CLIENT_ID=... OZON_API_KEY=... OZON_API_KEY_EXPIRES=2026-12-30
    python -m osbi.extract --since 2026-09-01 --to 2026-09-30 --out data/live

Каждый день начислений пишется отдельным файлом raw/accruals/YYYY-MM-DD.json —
повторная выгрузка дня перезаписывает файл, а загрузка дедуплицирует по accrual_id.
Журнал запусков (_runs.jsonl) хранит не «шаг прошёл», а свежесть данных:
максимальную дату и число записей по каждому источнику.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from .ozon_client import OzonCredentials, OzonSellerClient


def extract_accruals(client: OzonSellerClient, since: date, to: date, out: Path) -> dict:
    folder = out / "raw" / "accruals"
    folder.mkdir(parents=True, exist_ok=True)
    total, last_nonempty = 0, None
    d = since
    while d <= to:
        rows = list(client.accruals_by_day(d))
        (folder / f"{d.isoformat()}.json").write_text(
            json.dumps({"date": d.isoformat(), "accruals": rows, "last_id": ""}, ensure_ascii=False), "utf-8")
        total += len(rows)
        if rows:
            last_nonempty = d
        d += timedelta(days=1)
    types = client.accrual_types()
    (out / "raw" / "accrual_types.json").write_text(json.dumps({"types": types}, ensure_ascii=False), "utf-8")
    return {"source": "accruals", "rows": total, "max_date": last_nonempty.isoformat() if last_nonempty else None}


def extract_postings(client: OzonSellerClient, since: date, to: date, out: Path) -> dict:
    folder = out / "raw" / "postings_fbo"
    folder.mkdir(parents=True, exist_ok=True)
    rows = list(client.postings_fbo(datetime.combine(since, datetime.min.time()),
                                    datetime.combine(to, datetime.max.time().replace(microsecond=0))))
    (folder / f"{since.isoformat()}_{to.isoformat()}.json").write_text(
        json.dumps(rows, ensure_ascii=False), "utf-8")
    created = [r.get("created_at", "")[:10] for r in rows if r.get("created_at")]
    return {"source": "postings_fbo", "rows": len(rows), "max_date": max(created) if created else None}


def main() -> None:
    ap = argparse.ArgumentParser(description="Выгрузка Ozon Seller API в raw-слой")
    ap.add_argument("--since", required=True, type=date.fromisoformat)
    ap.add_argument("--to", required=True, type=date.fromisoformat)
    ap.add_argument("--out", default="data/live", type=Path)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = OzonSellerClient(OzonCredentials.from_env())
    runs = [extract_accruals(client, args.since, args.to, args.out),
            extract_postings(client, args.since, args.to, args.out)]
    with (args.out / "_runs.jsonl").open("a", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps({"run_at": datetime.now().isoformat(timespec="seconds"), **r},
                               ensure_ascii=False) + "\n")
            logging.info("%s: %s записей, последняя дата %s", r["source"], r["rows"], r["max_date"])


if __name__ == "__main__":
    main()
