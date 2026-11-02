"""Generación/publicación del pronóstico y pedido, y seguimiento del desempeño."""
from __future__ import annotations

import json
import os
import subprocess

import pandas as pd

from . import business, features, modeling
from .config import load_params, params_hash, path, project_root
from .ingest import as_of


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root(), capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


def prepare(cutoff: str | pd.Timestamp, params: dict | None = None) -> dict:
    """Prepara la matriz de features del corte (solo información disponible) y la persiste."""
    params = params or load_params()
    cutoff = pd.Timestamp(cutoff)
    H = params["forecast"]["horizon_days"]
    snap = as_of(cutoff, params)
    panel = modeling.make_panel(snap, cutoff, H)
    feats = features.build(panel, cutoff, H, with_target=False)
    out = path(params, "features_dir") / f"cutoff={cutoff:%Y-%m-%d}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    feats.to_parquet(tmp, index=False)
    os.replace(tmp, out)
    watermark = {s: f"{snap[s]['date'].max():%Y-%m-%d}" for s in ("sales", "transactions", "oil")}
    return {"cutoff": f"{cutoff:%Y-%m-%d}", "rows": int(len(feats)), "data_watermark": watermark}


def forecast(cutoff: str | pd.Timestamp, params: dict | None = None) -> dict:
    """Pronóstico + pedido para un corte, usando solo la información disponible en ese corte.

    Escribe en data/forecasts/cutoff=<fecha>/ (sobrescribe: re-ejecutar da el mismo resultado).
    """
    params = params or load_params()
    cutoff = pd.Timestamp(cutoff)
    fp = params["forecast"]
    H, qs = fp["horizon_days"], fp["quantiles"]
    snap = as_of(cutoff, params)
    panel = modeling.make_panel(snap, cutoff, H)
    champ = modeling.champion_at(cutoff, params)
    model = modeling.load_model(champ, params)
    if model is None:
        fc = features.baseline_quantiles(panel, cutoff, H, qs)
    else:
        prepared = path(params, "features_dir") / f"cutoff={cutoff:%Y-%m-%d}.parquet"
        feats = (pd.read_parquet(prepared) if prepared.exists()
                 else features.build(panel, cutoff, H, with_target=False))
        fc = model.predict(feats)
    fc["model_id"] = champ["model_id"]
    for c in business.quantile_cols(qs):
        fc[c] = fc[c].round(3)
    orders = business.weekly_orders(fc, qs, params["costs"], fp["order_coverage_days"])

    out = path(params, "forecasts_dir") / f"cutoff={cutoff:%Y-%m-%d}"
    out.mkdir(parents=True, exist_ok=True)
    fc.sort_values(["store_nbr", "family", "date"]).to_csv(out / "forecast.csv", index=False)
    orders.sort_values(["store_nbr", "family"]).to_csv(out / "orders.csv", index=False)
    watermark = {s: f"{snap[s]['date'].max():%Y-%m-%d}" for s in ("sales", "transactions", "oil")}
    meta = {"cutoff": f"{cutoff:%Y-%m-%d}", "model_id": champ["model_id"], "horizon_days": H,
            "data_watermark": watermark, "params_hash": params_hash(params), "git_commit": _git_commit(),
            "rows": int(len(fc))}
    (out / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def monitor(run_date: str | pd.Timestamp, params: dict | None = None) -> pd.DataFrame:
    """Compara todos los pronósticos publicados con las ventas reales conocidas a run_date.

    Usa la versión más reciente (corregida) de las ventas; por eso las métricas de un
    corte pueden cambiar cuando llega una corrección.
    """
    params = params or load_params()
    run_date = pd.Timestamp(run_date)
    qs = params["forecast"]["quantiles"]
    actual = as_of(run_date, params)["sales"][["store_nbr", "family", "date", "sales"]].rename(columns={"sales": "y"})
    rows = []
    for d in sorted(path(params, "forecasts_dir").glob("cutoff=*")):
        cutoff = pd.Timestamp(d.name.split("=", 1)[1])
        if cutoff > run_date:
            continue
        fc = pd.read_csv(d / "forecast.csv", parse_dates=["date"])
        merged = fc.merge(actual, on=["store_nbr", "family", "date"], how="left")
        known = merged["y"].notna()
        row = {"cutoff": f"{cutoff:%Y-%m-%d}", "model_id": fc["model_id"].iloc[0],
               "days_observed": int(merged.loc[known, "date"].nunique()),
               "rows_expected": int(len(merged))}
        if known.any():
            row.update(business.metrics(merged, qs, params["costs"]))
        rows.append(row)
    rep = pd.DataFrame(rows)
    rdir = path(params, "reports_dir") / "monitoring"
    rdir.mkdir(parents=True, exist_ok=True)
    rep.to_csv(rdir / f"monitoring_asof_{run_date:%Y-%m-%d}.csv", index=False)
    rep.to_csv(rdir / "monitoring_latest.csv", index=False)
    return rep


def monitor_summary(rep: pd.DataFrame) -> dict:
    """Resumen serializable (sin NaN) del seguimiento, para XCom/logs."""
    evaluated = rep[rep["days_observed"] > 0] if len(rep) else rep
    out = {"cortes_publicados": int(len(rep)), "cortes_con_reales": int(len(evaluated))}
    if len(evaluated):
        last = evaluated.iloc[-1]
        out.update({"ultimo_corte_evaluado": str(last["cutoff"]), "wape": round(float(last["wape"]), 4),
                    "cobertura_10_90": round(float(last["coverage_10_90"]), 4)})
    return out
