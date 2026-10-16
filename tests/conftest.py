import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Сгенерировать демо-данные и собрать витрины один раз на всю сессию тестов."""
    from osbi import demo_data
    from osbi.build import build

    tmp = tmp_path_factory.mktemp("demo")
    data_dir = demo_data.generate(tmp / "data")
    con = build(data_dir, tmp / "osbi.duckdb", render=False)
    return con, data_dir
