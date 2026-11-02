"""Construye la carga histórica inicial y la bandeja que simula el sistema de origen.

Formato de la bandeja (contrato con el sistema de origen):

    data/inbox/<fuente>/arrival=<YYYY-MM-DD>/<fuente>_<fecha_dato>_v<N>.csv

- ``arrival`` es el día en que el archivo quedó disponible. El flujo, al procesar
  la fecha lógica D, solo puede ver archivos con ``arrival <= D``.
- ``fecha_dato`` es el día al que se refieren los registros.
- ``vN`` es la versión: una corrección es el mismo ``fecha_dato`` con N mayor y
  una llegada posterior. Gana la versión más alta disponible.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from .config import load_params, path

SOURCES = ("sales", "transactions", "oil", "holidays")


def read_kaggle(kaggle_dir: Path) -> dict[str, pd.DataFrame]:
    sales = pd.read_csv(kaggle_dir / "train.csv", parse_dates=["date"]).drop(columns=["id"])
    return {
        "sales": sales,
        "stores": pd.read_csv(kaggle_dir / "stores.csv"),
        "holidays": pd.read_csv(kaggle_dir / "holidays_events.csv", parse_dates=["date"]),
        "oil": pd.read_csv(kaggle_dir / "oil.csv", parse_dates=["date"]),
        "transactions": pd.read_csv(kaggle_dir / "transactions.csv", parse_dates=["date"]),
    }


def build_history(raw: dict[str, pd.DataFrame], history_end: pd.Timestamp, out_dir: Path) -> None:
    """Carga histórica inicial: todo lo conocido hasta ``history_end`` (inclusive)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("sales", "transactions", "oil"):
        df = raw[name]
        df[df["date"] <= history_end].to_parquet(out_dir / f"{name}.parquet", index=False)
    # Los feriados pasados ya se conocen; los futuros llegan por la bandeja con anticipación.
    hol = raw["holidays"]
    hol[hol["date"] <= history_end].to_parquet(out_dir / "holidays.parquet", index=False)
    raw["stores"].to_parquet(out_dir / "stores.parquet", index=False)


def _write(inbox: Path, source: str, arrival: pd.Timestamp, data_date: pd.Timestamp,
           version: int, df: pd.DataFrame) -> None:
    d = inbox / source / f"arrival={arrival:%Y-%m-%d}"
    d.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out.to_csv(d / f"{source}_{data_date:%Y-%m-%d}_v{version}.csv", index=False, lineterminator="\n")


def build_inbox(raw: dict[str, pd.DataFrame], params: dict, inbox: Path) -> dict:
    sim = params["simulation"]
    rng = np.random.default_rng(sim["seed"])
    start, end = pd.Timestamp(sim["inbox_start"]), pd.Timestamp(sim["inbox_end"])
    lag = sim["lag_days"]
    if inbox.exists():
        shutil.rmtree(inbox)
    summary = {"files": 0, "corrections": [], "invalid": []}

    sales = raw["sales"][(raw["sales"]["date"] >= start) & (raw["sales"]["date"] <= end)]
    days = sorted(sales["date"].unique())
    n_corr = int(round(len(days) * sim["correction_rate"]))
    corr_days = set(pd.Timestamp(d) for d in rng.choice(days, size=n_corr, replace=False))
    invalid_sales_day = pd.Timestamp(days[len(days) // 2]) if sim["inject_invalid_files"] else None

    for day, df in sales.groupby("date"):
        day = pd.Timestamp(day)
        arrival = day + pd.Timedelta(days=lag["sales"])
        if day == invalid_sales_day:
            # v1 llega sin la columna 'sales' (error de esquema); el origen reenvía v2 válida.
            _write(inbox, "sales", arrival, day, 1, df.drop(columns=["sales"]))
            _write(inbox, "sales", arrival + pd.Timedelta(days=2), day, 2, df)
            summary["invalid"].append(f"sales {day:%Y-%m-%d} v1 (esquema)")
            summary["files"] += 2
        elif day in corr_days:
            # v1 llega con una tienda reportada en cero; v2 corrige con el valor real.
            bad = df.copy()
            store = int(rng.choice(bad["store_nbr"].unique()))
            bad.loc[bad["store_nbr"] == store, "sales"] = 0.0
            _write(inbox, "sales", arrival, day, 1, bad)
            _write(inbox, "sales", arrival + pd.Timedelta(days=sim["correction_delay_days"]), day, 2, df)
            summary["corrections"].append(f"sales {day:%Y-%m-%d} tienda {store}")
            summary["files"] += 2
        else:
            _write(inbox, "sales", arrival, day, 1, df)
            summary["files"] += 1

    trx = raw["transactions"][(raw["transactions"]["date"] >= start) & (raw["transactions"]["date"] <= end)]
    trx_days = sorted(trx["date"].unique())
    invalid_trx_day = pd.Timestamp(trx_days[len(trx_days) // 3]) if sim["inject_invalid_files"] else None
    for day, df in trx.groupby("date"):
        day = pd.Timestamp(day)
        if day == invalid_trx_day:
            df = df.copy()
            df.loc[df.index[:3], "transactions"] = -5  # valores imposibles: debe ir a cuarentena
            summary["invalid"].append(f"transactions {day:%Y-%m-%d} v1 (negativos)")
        _write(inbox, "transactions", day + pd.Timedelta(days=lag["transactions"]), day, 1, df)
        summary["files"] += 1

    oil = raw["oil"][(raw["oil"]["date"] >= start) & (raw["oil"]["date"] <= end)]
    wd = sim["oil_weekly_arrival_weekday"]
    for day, df in oil.groupby("date"):
        day = pd.Timestamp(day)
        ahead = (wd - day.weekday()) % 7 or 7  # siguiente lunes estrictamente posterior
        _write(inbox, "oil", day + pd.Timedelta(days=ahead), day, 1, df)
        summary["files"] += 1

    hol = raw["holidays"][raw["holidays"]["date"] >= start]
    for day, df in hol.groupby("date"):
        day = pd.Timestamp(day)
        _write(inbox, "holidays", day + pd.Timedelta(days=lag["holidays"]), day, 1, df)
        summary["files"] += 1
    return summary


def run(params: dict | None = None) -> dict:
    params = params or load_params()
    raw = read_kaggle(path(params, "kaggle_dir"))
    build_history(raw, pd.Timestamp(params["simulation"]["history_end"]), path(params, "history_dir"))
    return build_inbox(raw, params, path(params, "inbox_dir"))
