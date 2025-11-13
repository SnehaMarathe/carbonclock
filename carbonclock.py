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

# ========= Configuration (no sidebar) =========
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
refresh     = 5.0
ui_offset   = 1000.0

# ========= CSS: MAX font size + center alignment =========
st.markdown("""
<style>
/* Hide menu/footer */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

/* Fully center everything */
.block-container {
    max-width: 100% !important;
    padding-top: 5vh;
    text-align: center !important;
}

/* Huge number */
.big-number {
    font-size: 13rem;         /* MAX SIZE */
    font-weight: 900;
    color: #0A74DA;           /* Blue - change if needed */
    line-height: 1.1;
}

/* Label text */
.label-text {
    font-size: 3rem;
    font-weight: 500;
    color: #555;
    margin-bottom: 20px;
}
</style>
""", unsafe_allow_html=True)

# ================= Helpers =================
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

def walk_keys(obj: Any, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = f"{prefix}.{k}" if prefix else k
            yield from walk_keys(v, nk)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_keys(v, prefix)
    else:
        yield prefix, obj

def detect_fuel_key(sample_rows):
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
            found = False
            for k, v in walk_keys(cur):
                if k.lower() == dotted.lower():
                    cur = v; found = True; break
            if not found:
                return None
    try:
        if isinstance(cur, (int, float)): return float(cur)
        if isinstance(cur, str):
            s = cur.strip().replace(",", "")
            return float(s) if s else None
    except:
        return None
    return None

def lng_to_kg(v, unit, density):
    return v if unit.lower() == "kg" else v * density

def fetch_and_sum(token, acc_id, spec_ids, psize, lang, no_def,
                  proj, groups, lastloc, lng_unit, lng_density):
    url = BASE_URL + PATH
    headers = build_headers(token)
    total_input = 0
    fuel_key = None
    pnum = 1
    with requests.Session() as s:
        while True:
            params = {
                "pnum": pnum, "psize": psize,
                "no_default_fields": str(no_def).lower(),
                "proj": proj, "spec_ids": spec_ids,
                "groups": groups, "lastloc": str(lastloc).lower(),
                "acc_id": acc_id, "lang": lang,
            }
            r = s.get(url, params=params, headers=headers, timeout=45)
            r.raise_for_status()
            rows = list(iter_payload_rows(r.json()))
            if not rows: break
            if fuel_key is None:
                fuel_key = detect_fuel_key(rows[:10])
                if not fuel_key:
                    raise RuntimeError("Fuel field not detected")
            for rw in rows:
                v = get_value_by_dotted(rw, fuel_key)
                if v: total_input += v
            if len(rows) < psize: break
            pnum += 1
    total_lng = lng_to_kg(total_input, lng_unit, lng_density)
    return (total_lng * SAVINGS_PER_KG) / 1000.0

# ======== UI ========
st.set_page_config(layout="wide")

label_box = st.empty()
number_box = st.empty()

if not token:
    label_box.markdown("<div class='label-text'>INTANGLES_TOKEN missing</div>", unsafe_allow_html=True)
    number_box.markdown("<div class='big-number'>---</div>", unsafe_allow_html=True)
    st.stop()

latest_val = 0.0

# ========= Update Loop =========
while True:
    try:
        v = fetch_and_sum(
            token, acc_id, spec_ids, psize, lang, no_def,
            proj, groups, lastloc, lng_unit, lng_density
        )
        latest_val = max(latest_val, v)
    except Exception as e:
        print("Intangles API error:", e)

    val = latest_val + ui_offset

    label_box.markdown(
        "<div class='label-text'>Total tCO₂ saved (tons)</div>",
        unsafe_allow_html=True
    )

    number_box.markdown(
        f"<div class='big-number'>{val:,.3f}</div>",
        unsafe_allow_html=True
    )

    time.sleep(refresh)
