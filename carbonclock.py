# app.py
import os
import time
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

# ==== constants ====
BASE_URL = "https://apis.intangles.com"
PATH = "/vehicle/fuel_consumed"
SAVINGS_PER_KG = 0.926
REFERER = "https://bemblueedge.intangles.com/"
ORIGIN = "https://bemblueedge.intangles.com"
PREFERRED_KEYS = [
    "total_fuel_consumed",
    "data.total_fuel_consumed",
    "fuel_consumed",
    "total_fuel",
    "fuel_total",
    "fuel",
]

# ==== helpers ====
def build_headers(token: str) -> Dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "intangles-session-type": "web",
        "intangles-user-lang": "en",
        "intangles-user-token": token,
        "intangles-user-tz": "Asia/Calcutta",
        "Referer": REFERER,
        "Origin": ORIGIN,
        "User-Agent": "python-requests/2.x",
    }


def iter_payload_rows(payload: Any):
    if isinstance(payload, list):
        for x in payload:
            if isinstance(x, dict):
                yield x
        return

    if isinstance(payload, dict):
        for key in ("result", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                for x in val:
                    if isinstance(x, dict):
                        yield x
                return
            if isinstance(val, dict):
                yield val
                return
        # fallback: treat dict as a single row
        yield payload


def walk_keys(obj: Any, prefix: str = ""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = f"{prefix}.{k}" if prefix else k
            yield from walk_keys(v, nk)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v, prefix)
    else:
        yield prefix, obj


def detect_fuel_key(sample_rows: List[Dict[str, Any]]) -> Optional[str]:
    candidates = {
        k.lower()
        for row in sample_rows
        for k, v in walk_keys(row)
        if k and isinstance(v, (int, float, str)) and v is not None
    }

    # preferred known keys first
    for pref in PREFERRED_KEYS:
        if pref.lower() in candidates:
            return pref

    # heuristic fallback
    for k in candidates:
        if "fuel" in k and ("consum" in k or "total" in k):
            return k

    return None


def get_value_by_dotted(row: Dict[str, Any], dotted: str) -> Optional[float]:
    parts = dotted.split(".")
    cur: Any = row

    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            # fallback: exact dotted path search (case-insensitive)
            found = False
            for k, v in walk_keys(cur):
                if k.lower() == dotted.lower():
                    cur = v
                    found = True
                    break
            if not found:
                return None

    try:
        if cur is None:
            return None
        if isinstance(cur, (int, float)):
            return float(cur)
        if isinstance(cur, str):
            s = cur.strip().replace(",", "")
            return float(s) if s else None
    except Exception:
        return None

    return None


def lng_to_kg(v: float, unit: str, density_kg_per_L: float) -> float:
    if unit.lower() == "kg":
        return float(v)
    if unit.lower() == "l":
        return float(v) * float(density_kg_per_L)
    raise ValueError("Invalid LNG unit")


def fetch_and_sum(
    token: str,
    acc_id: str,
    spec_ids: str,
    psize: int,
    lang: str,
    no_default_fields: bool,
    proj: str,
    groups: str,
    lastloc: bool,
    lng_unit: str,
    lng_density: float,
) -> float:
    """
    Backend-style helper:
    Calls Intangles API, detects correct fuel field, sums all pages,
    converts to LNG kg and then to tCO₂ saved.
    """
    url = BASE_URL + PATH
