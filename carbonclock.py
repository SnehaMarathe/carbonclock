# app.py
import os
import time
import streamlit as st
from typing import Any, Dict, List, Optional
import requests

# ==== constants ====
BASE_URL = "https://apis.intangles.com"
PATH     = "/vehicle/fuel_consumed"
SAVINGS_PER_KG = 0.926
REFERER = "https://bemblueedge.intangles.com/"
ORIGIN  = "https://bemblueedge.intangles.com"
PREFERRED_KEYS = [
    "total_fuel_consumed",
    "data.total_fuel_consumed",
    "fuel_consumed",
    "total_fuel",
    "fuel_total",
    "fuel",
]

# ========= Fixed configuration (no sidebar) =========
# Set INTANGLES_TOKEN in Streamlit Cloud -> Settings -> Secrets:
# INTANGLES_TOKEN = "your_real_token_here"
token       = os.getenv("INTANGLES_TOKEN", "")
acc_id      = "962759605811675136"
spec_ids    = "966986020958502912,969208267156750336"
psize       = 300
lang        = "en"
no_def      = True
proj        = "total_fuel_consumed"
groups      = ""
lastloc     = True
lng_unit    = "kg"
lng_density = 0.45
refresh     = 1.0      # seconds between updates
ui_offset   = 1000.0   # tons added before display


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
        # fallback: treat dict as one row if it looks like data
        if any(isinstance(v, (int, float, str, dict, list)) for v in payload.values()):
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
    candidates: Dict[str, None] = {}
    for row in sample_rows:
        for k, v in walk_keys(row):
            if not k:
                continue
            if isinstance(v, (int, float, str)) and v is not None:
                candidates[k.lower()] = None
    lowers = set(candidates.keys())
    for pref in PREFERRED_KEYS:
        if pref.lower() in lowers:
            return pref
    for k in lowers:
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
    Backend: call Intangles, detect fuel field, sum pages,
    convert to LNG kg and then to tCO₂ saved.
    """
    url = BASE_URL + PATH
    headers = build_headers(token)
    total_input = 0.0
    fuel_key: Optional[str] = None
    pnum = 1

    with requests.Session() as s:
        while True:
            params = {
                "pnum": pnum,
                "psize": psize,
                "no_default_fields": str(no_default_fields).lower(),
                "proj": proj,
                "spec_ids": spec_ids,
                "groups": groups,
                "lastloc": str(lastloc).lower(),
                "acc_id": acc_id,
                "lang": lang,
            }
            resp = s.get(url, params=params, headers=headers, timeout=45)
            resp.raise_for_status()
            payload = resp.json()
            rows = list(iter_payload_rows(payload))
            if not rows:
                break

            if fuel_key is None:
                sample = rows[:10]
                detected = detect_fuel_key(sample)
                if not detected:
                    raise RuntimeError("Could not detect a fuel field in response.")
                fuel_key = detected

            page_sum = 0.0
            for r in rows:
                v = get_value_by_dotted(r, fuel_key)
                if v is not None:
                    page_sum += v
            total_input += page_sum

            if len(rows) < psize:
                break
            pnum += 1

    total_lng_kg = lng_to_kg(total_input, lng_unit, lng_density)
    total_tco2_saved = (total_lng_kg * SAVINGS_PER_KG) / 1000.0
    return total_tco2_saved


# ========= Streamlit UI (no sidebar) =========
st.set_page_config(layout="wide")
st.title("Blue Energy Motors – Real-Time CO₂ Saved")

# optionally hide Streamlit menu/footer for a clean kiosk view
st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    </style>
    """,
    unsafe_allow_html=True,
)

metric_placeholder = st.empty()

# if no token configured, show a static message and stop
if not token:
    metric_placeholder.metric(
        label="Total tCO₂ saved (tons)",
        value="INTANGLES_TOKEN not set",
    )
    st.stop()

# ========= Only the number updates in a loop =========
latest_val = 0.0  # never decrease within this run

while True:
    try:
        v = fetch_and_sum(
            token=token,
            acc_id=acc_id,
            spec_ids=spec_ids,
            psize=int(psize),
            lang=lang,
            no_default_fields=no_def,
            proj=proj,
            groups=groups,
            lastloc=lastloc,
            lng_unit=lng_unit,
            lng_density=float(lng_density),
        )
        latest_val = max(latest_val, v)
    except Exception as e:
        # keep previous value on error, log to server logs only
        print("Intangles API error:", repr(e))

    val_to_show = latest_val + float(ui_offset)
    metric_placeholder.metric(
        label="Total tCO₂ saved (tons)",
        value=f"{val_to_show:,.3f}",
    )

    time.sleep(float(refresh))

