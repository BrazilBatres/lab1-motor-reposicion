"""Ingesta y validación de la bandeja, y reconstrucción de la información "as-of".

La bandeja se lee pero nunca se modifica. Cada archivo válido se copia al lago
(``data/lake``) con su fecha de llegada y versión; los inválidos se registran en
cuarentena con el motivo. Todo es determinista: re-ejecutar la ingesta para la
misma fecha produce exactamente los mismos archivos.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .config import load_params, path

FILE_RE = re.compile(r"^(?P<source>[a-z_]+)_(?P<date>\d{4}-\d{2}-\d{2})_v(?P<version>\d+)\.csv$")

SCHEMAS = {
    "sales": ["date", "store_nbr", "family", "sales", "onpromotion"],
    "transactions": ["date", "store_nbr", "transactions"],
    "oil": ["date", "dcoilwtico"],
    "holidays": ["date", "type", "locale", "locale_name", "description", "transferred"],
}
KEYS = {
    "sales": ["date", "store_nbr", "family"],
    "transactions": ["date", "store_nbr"],
    "oil": ["date"],
    "holidays": ["date", "locale_name", "description"],
}


@dataclass(frozen=True)
class InboxFile:
    path: Path
    source: str
    data_date: pd.Timestamp
    arrival: pd.Timestamp
    version: int


def list_inbox(inbox: Path, until: pd.Timestamp | None = None) -> list[InboxFile]:
    files = []
    for f in sorted(inbox.glob("*/arrival=*/*.csv")):
        m = FILE_RE.match(f.name)
        if not m:
            continue
        arrival = pd.Timestamp(f.parent.name.split("=", 1)[1])
        if until is not None and arrival > until:
            continue
        files.append(InboxFile(f, m["source"], pd.Timestamp(m["date"]), arrival, int(m["version"])))
    return files


def validate(df: pd.DataFrame, source: str, data_date: pd.Timestamp, ref: dict) -> list[str]:
    """Devuelve la lista de errores (vacía = archivo válido)."""
    errors = []
    missing = [c for c in SCHEMAS[source] if c not in df.columns]
    if missing:
        return [f"faltan columnas: {missing}"]
    if df.empty:
        return ["archivo vacío"]
    dates = pd.to_datetime(df["date"], errors="coerce")
    if dates.isna().any():
        errors.append("fechas no interpretables")
    elif (dates != data_date).any():
        errors.append("la fecha de los registros no coincide con la del archivo")
    if df[KEYS[source]].isna().any().any():
        errors.append("llaves nulas")
    if df.duplicated(KEYS[source]).any():
        errors.append("llaves duplicadas")
    if source == "sales":
        s = pd.to_numeric(df["sales"], errors="coerce")
        p = pd.to_numeric(df["onpromotion"], errors="coerce")
        if s.isna().any() or (s < 0).any():
            errors.append("ventas nulas o negativas")
        if p.isna().any() or (p < 0).any():
            errors.append("onpromotion nulo o negativo")
        if not set(df["family"]).issubset(ref["families"]):
            errors.append("familias desconocidas")
    if source in ("sales", "transactions"):
        if not set(pd.to_numeric(df["store_nbr"], errors="coerce")).issubset(ref["stores"]):
            errors.append("tiendas desconocidas")
    if source == "transactions":
        t = pd.to_numeric(df["transactions"], errors="coerce")
        if t.isna().any() or (t < 0).any():
            errors.append("transacciones nulas o negativas")
    if source == "oil":
        # Un precio faltante (feriado en EE.UU.) es una situación esperada; un valor no numérico no.
        o = pd.to_numeric(df["dcoilwtico"], errors="coerce")
        if (o.isna() & df["dcoilwtico"].notna()).any() or (o <= 0).any():
            errors.append("precio de petróleo inválido")
    return errors


def reference_sets(params: dict) -> dict:
    hist = path(params, "history_dir")
    stores = pd.read_parquet(hist / "stores.parquet")
    fams = pd.read_parquet(hist / "sales.parquet", columns=["family"])["family"].unique()
    return {"stores": set(stores["store_nbr"].astype(int)), "families": set(fams)}


def ingest(run_date: str | pd.Timestamp, params: dict | None = None) -> dict:
    """Valida y copia al lago todos los archivos con llegada <= run_date."""
    params = params or load_params()
    run_date = pd.Timestamp(run_date)
    lake, quarantine = path(params, "lake_dir"), path(params, "quarantine_dir")
    ref = reference_sets(params)
    report = {"run_date": f"{run_date:%Y-%m-%d}", "valid": 0, "quarantined": []}
    for f in list_inbox(path(params, "inbox_dir"), until=run_date):
        out = lake / f.source / f"{f.data_date:%Y-%m-%d}_v{f.version}.parquet"
        qfile = quarantine / f.source / f"{f.data_date:%Y-%m-%d}_v{f.version}.json"
        try:
            df = pd.read_csv(f.path)
            errors = validate(df, f.source, f.data_date, ref)
        except Exception as exc:  # archivo ilegible
            df, errors = None, [f"no se pudo leer: {exc}"]
        if errors:
            qfile.parent.mkdir(parents=True, exist_ok=True)
            qfile.write_text(json.dumps({"file": f.path.name, "arrival": f"{f.arrival:%Y-%m-%d}",
                                         "errors": errors}, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            report["quarantined"].append({"file": f.path.name, "errors": errors})
            continue
        df["date"] = pd.to_datetime(df["date"])
        df["_arrival"] = f.arrival
        df["_version"] = f.version
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")  # escritura atómica: lectores concurrentes nunca ven archivos a medias
        df.to_parquet(tmp, index=False)
        os.replace(tmp, out)
        report["valid"] += 1
    rdir = path(params, "reports_dir") / "ingestion"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / f"{run_date:%Y-%m-%d}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                     encoding="utf-8", newline="\n")
    return report


def as_of(cutoff: str | pd.Timestamp, params: dict | None = None) -> dict[str, pd.DataFrame]:
    """Información disponible al inicio del día ``cutoff``.

    Historia + archivos del lago con llegada <= cutoff; para cada (fuente, fecha)
    se usa la versión más reciente disponible a esa fecha. Así un pronóstico
    pasado se reconstruye con exactamente la información que existía entonces.
    """
    params = params or load_params()
    cutoff = pd.Timestamp(cutoff)
    hist, lake = path(params, "history_dir"), path(params, "lake_dir")
    out = {"stores": pd.read_parquet(hist / "stores.parquet")}
    for source in ("sales", "transactions", "oil", "holidays"):
        base = pd.read_parquet(hist / f"{source}.parquet")
        latest: dict[str, tuple[pd.Timestamp, int, Path]] = {}
        for f in sorted((lake / source).glob("*.parquet")) if (lake / source).exists() else []:
            day, ver = f.stem.rsplit("_v", 1)
            arrival = pd.read_parquet(f, columns=["_arrival"])["_arrival"].iloc[0]
            if arrival > cutoff:
                continue
            key = (arrival, int(ver))
            if day not in latest or key > latest[day][:2]:
                latest[day] = (*key, f)
        parts = [base] + [pd.read_parquet(v[2]).drop(columns=["_arrival", "_version"])
                          for v in latest.values()]
        df = pd.concat(parts, ignore_index=True) if len(parts) > 1 else base
        # Por si un día de la bandeja se superpone con la historia, gana la bandeja.
        df = df.drop_duplicates(KEYS[source], keep="last").sort_values(KEYS[source]).reset_index(drop=True)
        out[source] = df
    return out
