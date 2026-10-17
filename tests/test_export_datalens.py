"""Выгрузка для DataLens: формат, который принимает подключение «Файлы», и сходимость цифр."""
import csv
import io
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def exported(built, tmp_path_factory):
    from osbi.export_datalens import export

    con, _ = built
    out = tmp_path_factory.mktemp("datalens")
    return export(con, out), out


def test_files_fit_datalens_connection_limits(exported):
    from osbi.export_datalens import MAX_FILES_PER_CONNECTION

    frames, out = exported
    files = sorted(out.glob("*.csv"))
    assert 0 < len(files) <= MAX_FILES_PER_CONNECTION
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert path.stat().st_size < 200 * 1024 * 1024
        header = next(csv.reader([text.splitlines()[0]]))
        assert len(header) == len(set(header)) <= 300, path.name
        assert "inf" not in text.lower().split(","), path.name


def test_month_is_text_yyyy_mm(exported):
    frames, _ = exported
    for name, df in frames.items():
        if "Месяц" in df.columns:
            assert df["Месяц"].str.fullmatch(r"\d{4}-\d{2}").all(), name


def test_pnl_lines_add_up_to_revenue_and_kpi(exported):
    frames, _ = exported
    sku, lines, kpi = frames["sku_month.csv"], frames["pnl_lines.csv"], frames["kpi_month.csv"]
    month = "2026-09"
    assert lines.loc[lines["Месяц"] == month, "Сумма"].sum() == pytest.approx(
        sku.loc[sku["Месяц"] == month, "Выручка"].sum(), abs=1)
    k = kpi.set_index("Месяц").loc[month]
    assert sku.loc[sku["Месяц"] == month, "Вклад в прибыль"].sum() == pytest.approx(k["Вклад в прибыль"], abs=1)
    assert k["Вклад в прибыль пред месяц"] == pytest.approx(
        kpi.set_index("Месяц").loc["2026-08", "Вклад в прибыль"])


def test_claims_match_html_dashboard(built, exported):
    from osbi.dashboard import money_findings

    con, _ = built
    frames, _ = exported
    _, claims_total = money_findings(con)
    fnd = frames["findings.csv"]
    assert fnd.loc[fnd["Тип"] == "Претензия к Ozon", "Сумма"].sum() == pytest.approx(claims_total, abs=1)


def test_committed_csv_match_fresh_export(exported):
    """CSV в репозитории (их загружают в DataLens) не отстают от витрин."""
    frames, _ = exported
    for name, df in frames.items():
        committed = pd.read_csv(ROOT / "datalens" / name)
        fresh = pd.read_csv(io.StringIO(df.to_csv(index=False)))
        pd.testing.assert_frame_equal(committed, fresh, check_exact=False, atol=0.01, obj=name)
