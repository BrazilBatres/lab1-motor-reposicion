import hashlib

import pandas as pd

from lab1 import features, ingest, modeling, product, simulate


def test_features_do_not_use_future_data(project):
    _, params = project
    simulate.run(params)
    ingest.ingest("2017-08-20", params)
    snap = ingest.as_of("2017-08-20", params)
    cutoff = pd.Timestamp("2017-07-10")
    f1 = features.build(modeling.make_panel(snap, cutoff, 14), cutoff, 14, with_target=False)
    tampered = dict(snap)
    s = snap["sales"].copy()
    s.loc[s["date"] >= cutoff, "sales"] *= 100  # alterar el futuro no debe cambiar nada
    tampered["sales"] = s
    f2 = features.build(modeling.make_panel(tampered, cutoff, 14), cutoff, 14, with_target=False)
    pd.testing.assert_frame_equal(f1, f2)


def test_weekly_forecast_is_reproducible_and_idempotent(project):
    root, params = project
    simulate.run(params)
    c = "2017-07-03"
    ingest.ingest(c, params)
    meta = modeling.train_and_register(pd.Timestamp(c), params)
    assert meta["model_id"] == "lgbm_2017-07-03"
    product.prepare(c, params)
    product.forecast(c, params)
    out = root / "data" / "forecasts" / f"cutoff={c}"
    h1 = hashlib.sha256((out / "forecast.csv").read_bytes()).hexdigest()
    # Llegan más datos (incluidas correcciones) y se re-ejecuta el mismo corte: mismo resultado.
    ingest.ingest("2017-08-20", params)
    product.forecast(c, params)
    assert hashlib.sha256((out / "forecast.csv").read_bytes()).hexdigest() == h1
    orders = pd.read_csv(out / "orders.csv")
    assert (orders["suggested_order"] >= 0).all() and len(orders) == 6
    rep = product.monitor("2017-08-20", params)
    assert rep.loc[0, "days_observed"] == 14 and rep.loc[0, "wape"] < 1
