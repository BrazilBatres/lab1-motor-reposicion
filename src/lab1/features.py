"""Construcción de features por fecha de corte, usando solo información disponible.

Para una fecha de corte C (inicio del día), se pronostican los días
t = C + h - 1, h = 1..H. Todas las features se calculan con datos de fechas
<= C - 1 que estén presentes en el snapshot "as-of" de C; si una fuente llegó
tarde, las ventanas simplemente tienen menos observaciones (nanmean).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NATIONAL_TYPES = {"Holiday", "Transfer", "Additional", "Bridge", "Event"}


class Panel:
    """Matrices densas serie x día para cálculo vectorizado de ventanas."""

    def __init__(self, snap: dict[str, pd.DataFrame], end: pd.Timestamp):
        sales = snap["sales"]
        self.start = pd.Timestamp(sales["date"].min())
        self.dates = pd.date_range(self.start, end)
        keys = sales[["store_nbr", "family"]].drop_duplicates().sort_values(["store_nbr", "family"])
        self.keys = keys.reset_index(drop=True)
        sidx = pd.MultiIndex.from_frame(self.keys)
        s = sales.set_index(["store_nbr", "family", "date"])
        self.y = self._pivot(s["sales"], sidx)
        self.promo = self._pivot(s["onpromotion"], sidx)
        self.logy = np.log1p(self.y)

        stores = snap["stores"].set_index("store_nbr")
        st = self.keys["store_nbr"].to_numpy()
        trx = snap["transactions"].pivot_table(index="store_nbr", columns="date", values="transactions")
        trx = trx.reindex(index=stores.index, columns=self.dates)
        self.trx = np.log1p(trx.loc[st].to_numpy(dtype=float))

        oil = snap["oil"].set_index("date")["dcoilwtico"].reindex(self.dates).astype(float)
        self.oil = oil.to_numpy()

        self.store_info = stores.loc[st].reset_index()
        self.holidays = snap["holidays"]

    def _pivot(self, s: pd.Series, sidx: pd.MultiIndex) -> np.ndarray:
        wide = s.unstack("date").reindex(index=sidx, columns=self.dates)
        return wide.to_numpy(dtype=float)

    def idx(self, d: pd.Timestamp) -> int:
        return int((pd.Timestamp(d) - self.start).days)


def _nanmean(a: np.ndarray) -> np.ndarray:
    with np.errstate(all="ignore"):
        cnt = np.sum(~np.isnan(a), axis=1)
        tot = np.nansum(a, axis=1)
        return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)


def _last_valid(a: np.ndarray) -> np.ndarray:
    out = np.full(a.shape[0], np.nan)
    for j in range(a.shape[1]):
        col = a[:, j]
        out = np.where(~np.isnan(col), col, out)
    return out


def holiday_flags(hol: pd.DataFrame, dates: pd.DatetimeIndex, store_info: pd.DataFrame):
    """Matriz (series x fechas) de feriado nacional y local, más Work Day."""
    h = hol[(hol["transferred"].astype(str) != "True")]
    nat = h[(h["locale"] == "National") & h["type"].isin(NATIONAL_TYPES)]["date"]
    nat_flag = dates.isin(pd.to_datetime(nat)).astype(float)
    loc = h[h["locale"] != "National"]
    local = np.zeros((len(store_info), len(dates)))
    pos = {d: i for i, d in enumerate(dates)}
    places = store_info["city"].to_numpy(), store_info["state"].to_numpy()
    for _, r in loc.iterrows():
        j = pos.get(pd.Timestamp(r["date"]))
        if j is None:
            continue
        local[:, j] = np.maximum(local[:, j], (places[0] == r["locale_name"]) | (places[1] == r["locale_name"]))
    return nat_flag, local


def build(panel: Panel, cutoff: pd.Timestamp, horizon: int, with_target: bool) -> pd.DataFrame:
    """Filas (serie, h) para una fecha de corte."""
    a = panel.idx(cutoff) - 1  # último día que podría conocerse
    n = len(panel.keys)
    ly, promo, trx = panel.logy[:, : a + 1], panel.promo[:, : a + 1], panel.trx[:, : a + 1]
    base = {
        "mean_7": _nanmean(ly[:, -7:]),
        "mean_14": _nanmean(ly[:, -14:]),
        "mean_28": _nanmean(ly[:, -28:]),
        "mean_56": _nanmean(ly[:, -56:]),
        "mean_182": _nanmean(ly[:, -182:]),
        "last_value": _last_valid(ly[:, -7:]),
        "zero_share_28": _nanmean((panel.y[:, a - 27: a + 1] == 0).astype(float)
                                  + np.where(np.isnan(panel.y[:, a - 27: a + 1]), np.nan, 0)),
        "promo_14": _nanmean(promo[:, -14:]),
        "trx_14": _nanmean(trx[:, -14:]),
    }
    oil_known = panel.oil[: a + 1]
    valid = oil_known[~np.isnan(oil_known)]
    oil_last = valid[-1] if len(valid) else np.nan
    oil_prev = valid[-21] if len(valid) >= 21 else np.nan

    tdates = pd.date_range(cutoff, periods=horizon)
    nat, local = holiday_flags(panel.holidays, tdates, panel.store_info)
    rows = []
    fam_codes = panel.keys["family"].astype("category").cat.codes.to_numpy()
    type_codes = panel.store_info["type"].astype("category").cat.codes.to_numpy()
    for h, t in enumerate(tdates, start=1):
        ti = panel.idx(t)
        # mismos días de la semana conocidos (<= a), últimas 4 semanas
        back = ti - 7 * int(np.ceil((ti - a) / 7))
        dow_idx = [back - 7 * k for k in range(4)]
        df = pd.DataFrame(base)
        df["dow_mean_4"] = _nanmean(panel.logy[:, dow_idx])
        df["h"] = h
        df["dow"] = t.day_of_week
        df["dom"] = t.day
        df["month"] = t.month
        df["payday"] = float(t.day == 15 or t.is_month_end)
        df["hol_nat"] = nat[h - 1]
        df["hol_local"] = local[:, h - 1]
        df["oil_last"] = oil_last
        df["oil_chg"] = oil_last / oil_prev - 1 if oil_prev == oil_prev else np.nan
        df["store_nbr"] = panel.keys["store_nbr"].to_numpy()
        df["family_code"] = fam_codes
        df["store_type"] = type_codes
        df["cluster"] = panel.store_info["cluster"].to_numpy()
        df["family"] = panel.keys["family"].to_numpy()
        df["date"] = t
        df["cutoff"] = cutoff
        if with_target:
            df["y"] = panel.y[:, ti] if ti < panel.y.shape[1] else np.nan
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


FEATURES = ["mean_7", "mean_14", "mean_28", "mean_56", "mean_182", "last_value", "zero_share_28",
            "promo_14", "trx_14", "dow_mean_4", "h", "dow", "dom", "month", "payday", "hol_nat",
            "hol_local", "oil_last", "oil_chg", "store_nbr", "family_code", "store_type", "cluster"]
CATEGORICAL = ["store_nbr", "family_code", "store_type", "cluster"]


def baseline_quantiles(panel: Panel, cutoff: pd.Timestamp, horizon: int, quantiles: list[float]) -> pd.DataFrame:
    """Baseline naive estacional: cuantiles empíricos de los últimos 8 mismos días de semana."""
    a = panel.idx(cutoff) - 1
    out = []
    for h, t in enumerate(pd.date_range(cutoff, periods=horizon), start=1):
        ti = panel.idx(t)
        back = ti - 7 * int(np.ceil((ti - a) / 7))
        vals = panel.y[:, [back - 7 * k for k in range(8)]]
        with np.errstate(all="ignore"):
            qs = np.nanquantile(vals, quantiles, axis=1).T
        df = pd.DataFrame(np.nan_to_num(qs), columns=[f"q{q}" for q in quantiles])
        df["store_nbr"] = panel.keys["store_nbr"].to_numpy()
        df["family"] = panel.keys["family"].to_numpy()
        df["date"], df["h"], df["cutoff"] = t, h, cutoff
        out.append(df)
    return pd.concat(out, ignore_index=True)
