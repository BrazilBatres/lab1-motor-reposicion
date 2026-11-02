import hashlib

import pandas as pd

from lab1 import ingest, simulate


def _hash_tree(root):
    h = hashlib.sha256()
    for f in sorted(root.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(root)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def test_validate_detects_invalid_data():
    ref = {"stores": {1, 2}, "families": {"DAIRY"}}
    d = pd.Timestamp("2017-06-01")
    good = pd.DataFrame({"date": ["2017-06-01"], "store_nbr": [1], "family": ["DAIRY"], "sales": [3.0],
                         "onpromotion": [0]})
    assert ingest.validate(good, "sales", d, ref) == []
    assert ingest.validate(good.drop(columns=["sales"]), "sales", d, ref)
    assert ingest.validate(good.assign(sales=-1.0), "sales", d, ref)
    assert ingest.validate(good.assign(store_nbr=99), "sales", d, ref)
    assert ingest.validate(good, "sales", pd.Timestamp("2017-06-02"), ref)  # fecha no coincide
    assert ingest.validate(pd.concat([good, good]), "sales", d, ref)        # duplicados


def test_oil_missing_price_is_expected_not_an_error():
    df = pd.DataFrame({"date": ["2017-07-04"], "dcoilwtico": [None]})
    assert ingest.validate(df, "oil", pd.Timestamp("2017-07-04"), {}) == []


def test_ingest_is_idempotent_and_never_modifies_inbox(project):
    root, params = project
    summary = simulate.run(params)
    inbox_before = _hash_tree(root / "data" / "inbox")
    r1 = ingest.ingest("2017-08-20", params)
    lake1 = _hash_tree(root / "data" / "lake")
    r2 = ingest.ingest("2017-08-20", params)
    assert _hash_tree(root / "data" / "lake") == lake1
    assert r1 == r2
    assert _hash_tree(root / "data" / "inbox") == inbox_before
    # los archivos inválidos inyectados se detienen en cuarentena
    assert len(r1["quarantined"]) == len(summary["invalid"]) == 2


def test_as_of_uses_only_available_information(project):
    _, params = project
    simulate.run(params)
    ingest.ingest("2017-08-20", params)
    snap = ingest.as_of("2017-08-01", params)
    assert snap["sales"]["date"].max() == pd.Timestamp("2017-07-31")        # ventas llegan con 1 día
    assert snap["transactions"]["date"].max() <= pd.Timestamp("2017-07-29")  # transacciones con 3 días


def test_correction_replaces_value_only_after_its_arrival(project):
    _, params = project
    summary = simulate.run(params)
    ingest.ingest("2017-08-25", params)
    parts = summary["corrections"][0].split()
    d, store = pd.Timestamp(parts[1]), int(parts[-1])
    lag, delay = params["simulation"]["lag_days"]["sales"], params["simulation"]["correction_delay_days"]

    def store_sales(cutoff):
        s = ingest.as_of(cutoff, params)["sales"]
        return s[(s["date"] == d) & (s["store_nbr"] == store)]["sales"].sum()

    assert store_sales(d + pd.Timedelta(days=lag + 1)) == 0
    assert store_sales(d + pd.Timedelta(days=lag + delay + 1)) > 0
