import numpy as np
import pandas as pd

from lab1 import business, modeling

QS = [0.1, 0.25, 0.5, 0.75, 0.9]
COSTS = {"perishable": {"underage": 1.0, "overage": 1.0, "families": ["DAIRY"]},
         "default": {"underage": 9.0, "overage": 1.0}}


def _fc():
    return pd.DataFrame({"family": ["DAIRY", "GROCERY I"], "h": [1, 1], "store_nbr": [1, 1],
                         "date": pd.to_datetime(["2017-07-03"] * 2),
                         "q0.1": [10, 10], "q0.25": [20, 20], "q0.5": [30, 30], "q0.75": [40, 40],
                         "q0.9": [50, 50]})


def test_order_uses_critical_ratio():
    q = business.order_quantity(_fc(), QS, COSTS)
    assert np.isclose(q[0], 30)  # ratio 0.5 -> mediana
    assert np.isclose(q[1], 50)  # ratio 0.9 -> p90


def test_changing_costs_changes_order_without_code_changes():
    cheap_stockout = {**COSTS, "default": {"underage": 1.0, "overage": 3.0}}  # ratio 0.25
    assert business.order_quantity(_fc(), QS, cheap_stockout)[1] < business.order_quantity(_fc(), QS, COSTS)[1]


def test_business_cost_metric():
    m = business.metrics(_fc().assign(y=[30.0, 60.0]), QS, COSTS)
    assert np.isclose(m["business_cost"], 9 * 10)


def test_champion_policy():
    s = pd.DataFrame({"model": ["baseline", "lgbm"], "wape": [0.20, 0.19], "business_cost": [100.0, 90.0]})
    policy = {"min_business_cost_improvement": 0.02, "max_wape_degradation": 0.01}
    assert modeling.decide(s, "baseline", "lgbm", policy)[0]
    assert not modeling.decide(s.assign(business_cost=[100.0, 99.0]), "baseline", "lgbm", policy)[0]
