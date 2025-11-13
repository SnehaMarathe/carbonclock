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
refresh     = 1.0     # seconds between updates
ui_offset   = 1000.0  # tons added before display

# ===== Streamlit basic config =====
st.set_page_config(layout="wide", page_title="CO₂ Saved – LED Counter")

# ========= Dark mode + LED styling =========
st.markdown("""
<style>
/* Import a nice digital-ish mono font */
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

/* Full dark background */
html, body, [data-testid="stAppViewContainer"], [data-testid="stApp"] {
    background-color: #000000 !important;
    color: #00ff99 !important;
}

/* Hide menu/footer/header for a kiosk look */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}

/* Center content */
.block-container {
    max-width: 100% !important;
    padding-top: 10vh;
    padding-left: 0;
    padding-right: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
}

/* Label */
.label-text {
    font-family: 'Share Tech Mono', monospace;
    font-size: 2.5rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: #00ffaa;
    text-align: center;
    margin-bottom: 20px;
}

/* Digital LED style number */
.led-number {
    font-family: 'Share Tech Mono', monospace;
    font-size: 14rem;               /* HUGE */
    font-weight: 400;
    color: #00ff99;
    text-shadow:
        0 0 8px  #00ff99,
        0 0 16px #00ff99,
        0 0 32px #00ff99,
        0 0 64px #00ff99;
    letter-spacing: 0.08em;
    text-align: center;
    margin: 0;
}

/* Subtle glow frame */
.led-frame {
    display: inline-block;
    padding: 40px 80px;
    border-radius: 24px;
    border: 2px solid #00ffaa33;
    box-shadow:
        0 0 20px #00ffaa33,
        0 0 60px #00ffaa22;
    background: radial-gradient(circle at top, #003322 0%, #000000 55%);
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
    for pref in PREFERRED_KEYS:
        if pref.lower() in candidates:
            return pref
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

# ======== UI skeleton ========
label_box = st.empty()
frame_box = st.empty()

if not token:
    label_box.markdown(
        "<div class='label-text'>INTANGLES_TOKEN missing</div>",
        unsafe_allow_html=True,
    )
    frame_box.markdown(
        "<div class='led-frame'><div class='led-number'>---</div></div>",
        unsafe_allow_html=True,
    )
    st.stop()

latest_val = 0.0

# ========= Update loop: only the LED number changes =========
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
        print("Intangles API error:", repr(e))

    val_to_show = latest_val + float(ui_offset)

    label_box.markdown(
        "<div class='label-text'>TOTAL tCO₂ SAVED (TONS)</div>",
        unsafe_allow_html=True,
    )
    frame_box.markdown(
        f"<div class='led-frame'><div class='led-number'>{val_to_show:,.3f}</div></div>",
        unsafe_allow_html=True,
    )

    time.sleep(float(refresh))

