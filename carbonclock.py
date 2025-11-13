# app.py
import os
import streamlit as st
from typing import Any, Dict, List, Optional
import requests
import time

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
    candidates = {k.lower() for row in sample_rows for k, v in walk_keys(row)
                  if isinstance(v, (int, float, str))}
    for pref in PREFERRED_KEYS:
        if pref.lower() in candidates:
            return pref
    for k in candidates:
        if "fuel" in k and ("consum" in k or "total" in k):
            return k
    return None

def get_value_by_dotted(row, dotted):
    parts = dotted.split(".")
    cur = row
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    try:
        if cur is None:
            return None
        if isinstance(cur, (int, float)):
            return float(cur)
        if isinstance(cur, str):
            s = cur.replace(",", "").strip()
            return float(s) if s else None
    except:
        return None

def lng_to_kg(v, unit, density):
    return v if unit.lower() == "kg" else v * density

# ====== API fetch with TTL cache (smooth refresh) ======
@st.cache_data(ttl=5)
def fetch_api_cached(token, acc_id, spec_ids, psize, lang, no_def,
                     proj, groups, lastloc, lng_unit, lng_density):

    url = BASE_URL + PATH
    headers = build_headers(token)

    total_input = 0.0
    fuel_key = None
    pnum = 1

    with requests.Session() as s:
        while True:
            params = {
                "pnum": pnum, "psize": psize,
                "no_default_fields": str(no_def).lower(),
                "proj": proj, "spec_ids": spec_ids,
                "groups": groups,
                "lastloc": str(lastloc).lower(),
                "acc_id": acc_id, "lang": lang,
            }
            r = s.get(url, params=params, headers=headers, timeout=45)
            r.raise_for_status()
            rows = list(iter_payload_rows(r.json()))
            if not rows:
                break

            if fuel_key is None:
                fuel_key = detect_fuel_key(rows[:10])
                if not fuel_key:
                    raise RuntimeError("Fuel field not found")

            total_input += sum(
                v for v in (get_value_by_dotted(rw, fuel_key) for rw in rows) if v
            )

            if len(rows) < psize:
                break
            pnum += 1

    total_lng_kg = lng_to_kg(total_input, lng_unit, lng_density)
    return (total_lng_kg * SAVINGS_PER_KG) / 1000.0


# ===== UI =====
st.set_page_config(layout="wide")
st.title("Blue Energy Motors – Real-Time CO₂ Saved")

with st.sidebar:
    st.header("Intangles API")
    token    = st.text_input("intangles-user-token", value=os.getenv("INTANGLES_TOKEN", ""), type="password")
    acc_id   = st.text_input("acc_id", value="962759605811675136")
    spec_ids = st.text_input("spec_ids", value="966986020958502912,969208267156750336")
    psize    = st.number_input("psize", 50, 2000, 300, 50)
    lang     = st.text_input("lang", value="en")
    no_def   = st.checkbox("no_default_fields", value=True)
    proj     = st.text_input("proj", value="total_fuel_consumed")
    groups   = st.text_input("groups", value="")
    lastloc  = st.checkbox("lastloc", value=True)
    lng_unit    = st.selectbox("LNG unit", ["kg", "L"], index=0)
    lng_density = st.number_input("LNG density", value=0.45, step=0.01)
    refresh_sec = st.slider("Refresh every (seconds)", 1, 30, 5)
    ui_offset   = st.number_input("UI offset (tons)", value=1000.0)

status = st.empty()

if token:
    try:
        v = fetch_api_cached(
            token, acc_id, spec_ids, int(psize), lang, no_def,
            proj, groups, lastloc, lng_unit, float(lng_density)
        )
        status.info("Live from Intangles API")
    except Exception as e:
        status.error(f"API error: {e}")
        v = 0
else:
    status.warning("Enter your token to start.")
    v = 0

# display
st.metric("Total tCO₂ saved (tons)", f"{v + ui_offset:,.3f}")

# soft refresh (no visual flashing)
time.sleep(refresh_sec)
st.rerun()
