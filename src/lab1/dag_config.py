"""Configuración compartida por los DAGs (fechas y argumentos por defecto) leída de params.yaml."""
from __future__ import annotations

from datetime import timedelta

import pendulum

from lab1.config import load_params

PARAMS = load_params()
SIM = PARAMS["simulation"]
START = pendulum.parse(SIM["inbox_start"], tz="UTC")
# El periodo a procesar se controla desde config/params.yaml (inbox_start / inbox_end).
END = pendulum.parse(SIM["inbox_end"], tz="UTC").add(days=PARAMS["forecast"]["horizon_days"])
DEFAULT_ARGS = {"owner": "lab1", "retries": 1, "retry_delay": timedelta(minutes=1)}
