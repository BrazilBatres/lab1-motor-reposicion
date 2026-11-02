"""Punto de entrada por línea de comandos: python -m lab1.cli <comando> [fecha]."""
from __future__ import annotations

import argparse
import json

import pandas as pd

from . import ingest, modeling, product, simulate
from .config import load_params, path


def run_weekly(cutoff: str, params: dict) -> dict:
    """Paso semanal completo (lo mismo que hace el DAG): ingesta, reentrenamiento si toca, pronóstico."""
    c = pd.Timestamp(cutoff)
    ingest.ingest(c, params)
    trained = modeling.train_and_register(c, params) if modeling.retrain_due(c, params) else None
    meta = product.forecast(c, params)
    return {"trained": trained, "forecast": meta}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["bootstrap", "ingest", "train", "forecast", "weekly", "monitor",
                                        "backtest", "simulate-all"])
    ap.add_argument("date", nargs="?")
    args = ap.parse_args(argv)
    params = load_params()
    if args.command == "bootstrap":
        res = simulate.run(params)
    elif args.command == "ingest":
        res = ingest.ingest(args.date, params)
    elif args.command == "train":
        res = modeling.train_and_register(pd.Timestamp(args.date), params)
    elif args.command == "forecast":
        res = product.forecast(args.date, params)
    elif args.command == "weekly":
        res = run_weekly(args.date, params)
    elif args.command == "monitor":
        res = product.monitor(args.date, params).to_dict(orient="records")
    elif args.command == "backtest":
        d = pd.Timestamp(args.date)
        bt = modeling.backtest(ingest.as_of(d, params), d, params)
        out = path(params, "reports_dir") / "model_comparison.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        bt.to_csv(out, index=False)
        res = modeling.summarize(bt).to_dict(orient="records")
    else:  # simulate-all: corre toda la bandeja día a día sin Airflow (útil para pruebas locales)
        sim = params["simulation"]
        res = []
        for d in pd.date_range(sim["inbox_start"], pd.Timestamp(sim["inbox_end"]) + pd.Timedelta(days=7)):
            if d.weekday() == params["forecast"]["cutoff_weekday"]:
                res.append(run_weekly(f"{d:%Y-%m-%d}", params))
            else:
                ingest.ingest(d, params)
        product.monitor(d, params)
    print(json.dumps(res, indent=1, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
