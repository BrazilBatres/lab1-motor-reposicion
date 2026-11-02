"""Reglas de negocio: costos, pedido sugerido (newsvendor) y métricas."""
from __future__ import annotations

import numpy as np
import pandas as pd


def unit_costs(families: pd.Series, costs: dict) -> tuple[np.ndarray, np.ndarray]:
    perishable = set(costs["perishable"]["families"])
    is_p = families.isin(perishable).to_numpy()
    cu = np.where(is_p, costs["perishable"]["underage"], costs["default"]["underage"])
    co = np.where(is_p, costs["perishable"]["overage"], costs["default"]["overage"])
    return cu.astype(float), co.astype(float)


def critical_ratio(families: pd.Series, costs: dict) -> np.ndarray:
    cu, co = unit_costs(families, costs)
    return cu / (cu + co)


def quantile_cols(quantiles: list[float]) -> list[str]:
    return [f"q{q}" for q in quantiles]


def order_quantity(fc: pd.DataFrame, quantiles: list[float], costs: dict) -> np.ndarray:
    """Cantidad diaria óptima = cuantil crítico cu/(cu+co), interpolado entre cuantiles pronosticados."""
    qs = np.sort(fc[quantile_cols(quantiles)].to_numpy(), axis=1)  # evita cruces de cuantiles
    r = critical_ratio(fc["family"], costs)
    levels = np.asarray(quantiles)
    r = np.clip(r, levels[0], levels[-1])
    hi = np.searchsorted(levels, r, side="left").clip(1, len(levels) - 1)
    lo = hi - 1
    w = (r - levels[lo]) / (levels[hi] - levels[lo])
    rows = np.arange(len(qs))
    return np.maximum(qs[rows, lo] * (1 - w) + qs[rows, hi] * w, 0.0)


def weekly_orders(fc: pd.DataFrame, quantiles: list[float], costs: dict, coverage_days: int) -> pd.DataFrame:
    fc = fc.copy()
    fc["order_daily"] = order_quantity(fc, quantiles, costs)
    cov = fc[fc["h"] <= coverage_days]
    out = cov.groupby(["store_nbr", "family"], as_index=False).agg(
        suggested_order=("order_daily", "sum"), forecast_p50=("q0.5", "sum"))
    out["critical_ratio"] = critical_ratio(out["family"], costs)
    out["coverage_start"] = cov["date"].min()
    out["coverage_end"] = cov["date"].max()
    out["suggested_order"] = out["suggested_order"].round(1)
    return out


def metrics(df: pd.DataFrame, quantiles: list[float], costs: dict) -> dict:
    """Métricas técnicas y de negocio sobre filas con 'y' observado."""
    df = df[df["y"].notna()]
    y = df["y"].to_numpy()
    p50 = np.maximum(df["q0.5"].to_numpy(), 0)
    pinball = []
    for q in quantiles:
        e = y - df[f"q{q}"].to_numpy()
        pinball.append(np.mean(np.maximum(q * e, (q - 1) * e)))
    order = order_quantity(df, quantiles, costs)
    cu, co = unit_costs(df["family"], costs)
    cost = cu * np.maximum(y - order, 0) + co * np.maximum(order - y, 0)
    lo, hi = quantiles[0], quantiles[-1]
    return {
        "n": int(len(df)),
        "wape": float(np.abs(y - p50).sum() / max(y.sum(), 1e-9)),
        "rmsle": float(np.sqrt(np.mean((np.log1p(p50) - np.log1p(y)) ** 2))),
        "pinball": float(np.mean(pinball)),
        f"coverage_{int(lo*100)}_{int(hi*100)}": float(np.mean((y >= df[f"q{lo}"]) & (y <= df[f"q{hi}"]))),
        "business_cost": float(cost.sum()),
        "business_cost_per_unit_sold": float(cost.sum() / max(y.sum(), 1e-9)),
    }
