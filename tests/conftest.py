"""Proyecto sintético pequeño (mismo formato que Kaggle) para pruebas rápidas y aisladas."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ["DAIRY", "GROCERY I", "PRODUCE"]


def _kaggle(d: Path) -> None:
    d.mkdir(parents=True)
    rng = np.random.default_rng(0)
    dates = pd.date_range("2016-06-01", "2017-08-15")
    rows = []
    for s in (1, 2):
        for f in FAMILIES:
            base = 50 * s + 10 * FAMILIES.index(f)
            for t in dates:
                if t.month == 12 and t.day == 25:
                    continue
                rows.append((t, s, f, float(rng.poisson(base * (1.3 if t.day_of_week >= 5 else 1))),
                             int(rng.integers(0, 3))))
    sales = pd.DataFrame(rows, columns=["date", "store_nbr", "family", "sales", "onpromotion"])
    sales.insert(0, "id", range(len(sales)))
    sales.to_csv(d / "train.csv", index=False)
    pd.DataFrame({"store_nbr": [1, 2], "city": ["Quito", "Guayaquil"], "state": ["Pichincha", "Guayas"],
                  "type": ["D", "A"], "cluster": [13, 1]}).to_csv(d / "stores.csv", index=False)
    pd.DataFrame({"date": ["2017-01-01", "2017-07-24", "2017-08-10"], "type": ["Holiday"] * 3,
                  "locale": ["National", "Local", "National"], "locale_name": ["Ecuador", "Guayaquil", "Ecuador"],
                  "description": ["Primer dia del ano", "Fundacion de Guayaquil", "Primer Grito"],
                  "transferred": [False, False, False]}).to_csv(d / "holidays_events.csv", index=False)
    wd = dates[dates.day_of_week < 5]
    pd.DataFrame({"date": wd, "dcoilwtico": 50 + rng.normal(0, 1, len(wd))}).to_csv(d / "oil.csv", index=False)
    trx = sales.groupby(["date", "store_nbr"], as_index=False)["sales"].sum()
    trx["transactions"] = (trx.pop("sales") * 3).astype(int)
    trx.to_csv(d / "transactions.csv", index=False)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """Copia la configuración real, reduce el modelo y apunta LAB1_ROOT a un directorio temporal."""
    params = yaml.safe_load((ROOT / "config" / "params.yaml").read_text(encoding="utf-8"))
    params["model"].update({"train_weeks": 4, "backtest_folds": 2})
    params["model"]["lgbm"].update({"n_estimators": 20, "num_leaves": 7, "min_child_samples": 5, "n_jobs": 1})
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "params.yaml").write_text(yaml.safe_dump(params), encoding="utf-8")
    _kaggle(tmp_path / "kaggle")
    monkeypatch.setenv("LAB1_ROOT", str(tmp_path))
    from lab1 import config
    config._load.cache_clear()
    yield tmp_path, config.load_params(tmp_path / "config" / "params.yaml")
    shutil.rmtree(tmp_path, ignore_errors=True)
