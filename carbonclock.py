# app.py
import os, time
import streamlit as st
from typing import Any, Dict, Iterable, List, Optional, Tuple
import requests

# ==== your constants ====
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

# ==== your helpers (unchanged) ====
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
                    cur = v; found = True; break
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

def fetch_and_sum(token: str,
                  acc_id: str,
                  spec_ids: str,
                  psize: int,
                  lang: str,
                  no_default_fields: bool,
                  proj: str,
                  groups: str,
                  lastloc: bool,
                  lng_unit: str,
                  lng_density: float) -> float:
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

# ==== Streamlit UI ====
st.set_page_config(layout="wide")
st.title("Blue Energy Motors – Real-Time CO₂ Saved")

with st.sidebar:
    st.header("Intangles API")
    token    = st.text_input("intangles-user-token", value=os.getenv("INTANGLES_TOKEN", ""), type="password")
    acc_id   = st.text_input("acc_id", value="962759605811675136")
    spec_ids = st.text_input("spec_ids (comma-separated)", value="966986020958502912,969208267156750336")
    psize    = st.number_input("psize", min_value=50, max_value=2000, value=300, step=50)
    lang     = st.text_input("lang", value="en")
    no_def   = st.checkbox("no_default_fields", value=True)
    proj     = st.text_input("proj", value="total_fuel_consumed")
    groups   = st.text_input("groups", value="")
    lastloc  = st.checkbox("lastloc", value=True)

    lng_unit    = st.selectbox("LNG unit returned", ["kg", "L"], index=0)
    lng_density = st.number_input("LNG density (kg/L if unit = L)", value=0.45, step=0.01, format="%.2f")

    refresh = st.slider("Refresh every (seconds)", 0.3, 5.0, 0.5, 0.1)
    ui_offset = st.number_input("UI offset (tons, added before display)", value=1000.0, step=100.0)

# sticky state: never decrease
if "latest_val" not in st.session_state:
    st.session_state.latest_val = 0.0

# fetch (once per run)
status = st.empty()
if token:
    try:
        v = fetch_and_sum(
            token=token, acc_id=acc_id, spec_ids=spec_ids, psize=int(psize), lang=lang,
            no_default_fields=no_def, proj=proj, groups=groups, lastloc=lastloc,
            lng_unit=lng_unit, lng_density=float(lng_density),
        )
        st.session_state.latest_val = max(st.session_state.latest_val, v)
        status.info("Live from Intangles API ✅")
    except Exception as e:
        status.warning(f"API error — showing last known value, will retry: {e}")
else:
    status.warning("Enter your Intangles token in the sidebar.")

# display (+ offset)
val = st.session_state.latest_val + float(ui_offset)
st.metric(label="Total tCO₂ saved (tons)", value=f"{val:,.3f}")

# schedule next run
time.sleep(max(0.1, float(refresh)))
st.rerun()

