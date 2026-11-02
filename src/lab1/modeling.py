"""Modelos (baseline y LightGBM por cuantiles), validación temporal y política de campeón."""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from . import business, features
from .config import load_params, path
from .ingest import as_of


def cutoff_on_or_before(d: pd.Timestamp, weekday: int) -> pd.Timestamp:
    d = pd.Timestamp(d)
    return d - pd.Timedelta(days=(d.weekday() - weekday) % 7)


def make_panel(snap: dict, cutoff: pd.Timestamp, horizon: int) -> features.Panel:
    end = max(pd.Timestamp(snap["sales"]["date"].max()), cutoff + pd.Timedelta(days=horizon))
    return features.Panel(snap, end)


def training_frame(panel: features.Panel, last_cutoff: pd.Timestamp, weeks: int, horizon: int) -> pd.DataFrame:
    """Ejemplos de entrenamiento: cortes semanales cuyo horizonte completo es < last_cutoff."""
    frames = []
    for k in range(weeks):
        c = last_cutoff - pd.Timedelta(weeks=k)
        if c - pd.Timedelta(days=200) < panel.start:
            break
        frames.append(features.build(panel, c, horizon, with_target=True))
    df = pd.concat(frames, ignore_index=True)
    return df[df["y"].notna()]


class LGBMQuantile:
    method = "lgbm"

    def __init__(self, quantiles: list[float], params: dict):
        self.quantiles = quantiles
        self.params = params
        self.models: dict[float, lgb.LGBMRegressor] = {}

    def fit(self, df: pd.DataFrame) -> "LGBMQuantile":
        X, y = df[features.FEATURES], np.log1p(df["y"])
        p = dict(self.params)
        seed = p.pop("seed", 42)
        for q in self.quantiles:
            m = lgb.LGBMRegressor(objective="quantile", alpha=q, random_state=seed, deterministic=True,
                                  force_row_wise=True, verbose=-1, **p)
            m.fit(X, y, categorical_feature=features.CATEGORICAL)
            self.models[q] = m
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df[["store_nbr", "family", "date", "h", "cutoff"]].copy()
        for q, m in self.models.items():
            out[f"q{q}"] = np.maximum(np.expm1(m.predict(df[features.FEATURES])), 0)
        return out


def lgbm_forecast(model: LGBMQuantile, panel, cutoff, horizon) -> pd.DataFrame:
    return model.predict(features.build(panel, cutoff, horizon, with_target=False))


def backtest(snap: dict, asof: pd.Timestamp, params: dict) -> pd.DataFrame:
    """Rolling origin: mismos cortes y mismas reglas para todos los modelos comparados."""
    fp, mp = params["forecast"], params["model"]
    H, qs = fp["horizon_days"], fp["quantiles"]
    panel = make_panel(snap, asof, H)
    last_eval = cutoff_on_or_before(asof - pd.Timedelta(days=H), fp["cutoff_weekday"])
    rows = []
    for k in range(mp["backtest_folds"]):
        c = last_eval - pd.Timedelta(weeks=2 * k)
        truth = features.build(panel, c, H, with_target=True)[["store_nbr", "family", "date", "y"]]
        train = training_frame(panel, c - pd.Timedelta(days=H), mp["train_weeks"], H)
        preds = {
            "baseline": features.baseline_quantiles(panel, c, H, qs),
            "lgbm": LGBMQuantile(qs, mp["lgbm"]).fit(train).predict(features.build(panel, c, H, False)),
        }
        for name, p in preds.items():
            m = business.metrics(p.merge(truth, on=["store_nbr", "family", "date"]), qs, params["costs"])
            rows.append({"fold_cutoff": f"{c:%Y-%m-%d}", "model": name, **m})
    return pd.DataFrame(rows)


def summarize(bt: pd.DataFrame) -> pd.DataFrame:
    return bt.groupby("model").agg(wape=("wape", "mean"), rmsle=("rmsle", "mean"), pinball=("pinball", "mean"),
                                   business_cost=("business_cost", "sum")).reset_index()


def decide(summary: pd.DataFrame, incumbent: str, challenger: str, policy: dict) -> tuple[bool, str]:
    """Criterio explícito de reemplazo del modelo vigente."""
    s = summary.set_index("model")
    if incumbent == challenger:
        # Reentrenamiento programado del mismo método: se acepta si sigue superando al baseline.
        incumbent = "baseline"
        if challenger == "baseline":
            return True, "baseline vigente"
    inc, ch = s.loc[incumbent], s.loc[challenger]
    gain = 1 - ch["business_cost"] / inc["business_cost"]
    wape_ok = ch["wape"] <= inc["wape"] + policy["max_wape_degradation"]
    ok = gain >= policy["min_business_cost_improvement"] and wape_ok
    reason = (f"{challenger} vs {incumbent}: mejora costo negocio {gain:.1%} "
              f"(mín {policy['min_business_cost_improvement']:.0%}), WAPE {ch['wape']:.3f} vs {inc['wape']:.3f}")
    return bool(ok), reason


# ---------------------------------------------------------------- registro de modelos

def _models_dir(params) -> Path:
    return path(params, "models_dir")


def registry(params) -> list[dict]:
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in _models_dir(params).glob("*/meta.json")]
    return sorted(metas, key=lambda m: m["trained_asof"])


def champion_at(cutoff: pd.Timestamp, params) -> dict:
    """Modelo vigente para un corte: el último promovido con trained_asof <= cutoff."""
    cands = [m for m in registry(params) if m["promoted"] and pd.Timestamp(m["trained_asof"]) <= cutoff]
    if cands:
        return cands[-1]
    return {"model_id": "baseline", "method": "baseline", "trained_asof": None, "promoted": True}


def retrain_due(cutoff: pd.Timestamp, params) -> bool:
    """Calendario fijo de reentrenamiento (cada N semanas desde el primer corte de la bandeja).

    Al depender solo de la fecha, el backfill reentrena exactamente en las mismas fechas.
    """
    fp, mp = params["forecast"], params["model"]
    first = cutoff_on_or_before(pd.Timestamp(params["simulation"]["inbox_start"]) + pd.Timedelta(days=6),
                                fp["cutoff_weekday"])
    weeks = (cutoff - first).days // 7
    return weeks >= 0 and weeks % mp["retrain_every_weeks"] == 0


def train_and_register(cutoff: pd.Timestamp, params: dict | None = None) -> dict:
    """Entrena el retador con la información as-of del corte y aplica la política de campeón."""
    params = params or load_params()
    cutoff = pd.Timestamp(cutoff)
    fp, mp = params["forecast"], params["model"]
    snap = as_of(cutoff, params)
    bt = backtest(snap, cutoff, params)
    summary = summarize(bt)
    incumbent = champion_at(cutoff - pd.Timedelta(days=1), params)["method"]
    promoted, reason = decide(summary, incumbent, "lgbm", params["champion_policy"])

    panel = make_panel(snap, cutoff, fp["horizon_days"])
    train = training_frame(panel, cutoff - pd.Timedelta(days=fp["horizon_days"]), mp["train_weeks"],
                           fp["horizon_days"])
    model = LGBMQuantile(fp["quantiles"], mp["lgbm"]).fit(train)
    model_id = f"lgbm_{cutoff:%Y-%m-%d}"
    d = _models_dir(params) / model_id
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "model.pkl", "wb") as fh:
        pickle.dump(model, fh)
    bt.to_csv(d / "backtest.csv", index=False, lineterminator="\n")
    meta = {"model_id": model_id, "method": "lgbm", "trained_asof": f"{cutoff:%Y-%m-%d}",
            "incumbent": incumbent, "promoted": promoted, "reason": reason,
            "summary": summary.round(4).to_dict(orient="records")}
    (d / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    return meta


def load_model(meta: dict, params) -> LGBMQuantile | None:
    if meta["method"] == "baseline":
        return None
    with open(_models_dir(params) / meta["model_id"] / "model.pkl", "rb") as fh:
        return pickle.load(fh)
