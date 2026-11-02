"""Carga de la configuración versionada y resolución de rutas."""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

import yaml


def project_root() -> Path:
    env = os.environ.get("LAB1_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_params(path: str | Path | None = None) -> dict:
    path = Path(path) if path else project_root() / "config" / "params.yaml"
    return _load(str(path))


def path(params: dict, key: str) -> Path:
    p = Path(params["paths"][key])
    return p if p.is_absolute() else project_root() / p


def params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
