#!/usr/bin/env python3
# streamlit run carbonclock.py
"""
Live tCO2 saved (tons) from Intangles /vehicle/fuel_consumed

- Reuses your robust, working fetch logic (no pandas; O(1) memory).
- Token pulled from st.secrets["INTANGLES_TOKEN"] or environment INTANGLES_TOKEN,
  or a text input box if neither is present.
- Auto-refresh using st.autorefresh (no experimental_rerun).
- Simple, readable UI with a big number and status line.
"""

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import streamlit as st

# ----------------------------- Intangles API settings -----------------------------
BASE_URL = "https://apis.intangles.com"
PATH     = "/vehicle/fuel_consumed"

# kg CO2 saved per kg LNG (platform-aligned)
SAVINGS_PER_KG = 0.926

REFERER = "https://bemblueedge.intangles.com/"
ORIGIN  = "https://bemblueedge.intangles.com"

# Same robust key detection you use
PREFERRED_KEYS = [
    "total_fuel_consumed",
    "data.total_fuel_consumed",
    "fuel_consumed",
    "total_fuel",
    "fuel_total",
    "fuel",
]

# --------------------------------- Helpers ---------------------------------
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

def iter_payload_rows(payload: Any) -> Iterable[Dict[str, Any]]:
    """Yield row-like dicts from common API shapes."""
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
        # fallback: maybe payload is itself a row dict
        if any(isinstance(v, (int, float, str, dict, list)) for v in payload.values()):
            yield payload

def walk_keys(obj: Any, prefix: str = "") -> Iterable[Tuple[str, Any]]:
    """Yield (dotted_key, value) for all leaf nodes in nested dict/list."""
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
    """Find a likely fuel key by preference and fuzzy contains."""
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

    # fuzzy: any key containing "fuel" and ("consum" or "total")
    for k in lowers:
        if "fuel" in k and ("consum" in k or "total" in k):
            return k

    return None

def get_value_by_dotted(row: Dict[str, Any], dotted: str) -> Optional[float]:
    """Retrieve numeric value at dotted path from a nested row."""
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
    raise ValueError("Invalid LNG unit. Use 'kg' or 'L'.")

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
    Stream pages and return TOTAL tCO2 saved (in tonnes).
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
                    sample_keys = sorted({k for r in sample for k, _ in walk_keys(r)})[:30]
                    raise RuntimeError(
                        "Could not detect a fuel field. Sample keys: " + ", ".join(sample_keys)
                    )
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

# ------------------------------- Streamlit UI -------------------------------
st.set_page_config(page_title="Live tCO₂ saved", page_icon="🌍", layout="wide")

# Sidebar controls
with st.sidebar:
    st.header("Settings")
    # Token priority: st.secrets → env → text_input
    token = st.secrets.get("INTANGLES_TOKEN", None) if hasattr(st, "secrets") else None
    if not token:
        token = os.getenv("INTANGLES_TOKEN", "")
    token = st.text_input("Intangles Token", value=token or "", type="password", help="You can also set st.secrets['INTANGLES_TOKEN'] or environment INTANGLES_TOKEN.")
    acc_id = st.text_input("Account ID (acc_id)", value="962759605811675136")
    spec_ids = st.text_input("spec_ids (comma-separated)", value="966986020958502912,969208267156750336")
    psize = st.number_input("Page size (psize)", min_value=50, max_value=1000, value=300, step=50)
    lang = st.text_input("lang", value="en")
    no_default_fields = st.checkbox("no_default_fields", value=True)
    proj = st.text_input("proj", value="total_fuel_consumed")
    groups = st.text_input("groups", value="")
    lastloc = st.checkbox("lastloc", value=True, help="Match the platform’s total")
    lng_unit = st.selectbox("LNG unit", options=["kg", "L"], index=0)
    lng_density = st.number_input("LNG density (kg/L if unit=L)", min_value=0.1, max_value=1.0, value=0.45, step=0.01)
    refresh = st.number_input("Refresh (seconds)", min_value=0.5, max_value=60.0, value=2.0, step=0.5)

# Auto refresh (ms)
st.autorefresh(interval=int(float(refresh) * 1000), key="poll_timer")

st.title("Live from Intangles API ✅")
st.caption("Total tCO₂ saved (tons)")

# Guard: token required
if not token:
    st.warning("Provide an Intangles token to start.")
    st.stop()

# Fetch section
col = st.container()
with col:
    try:
        t0 = time.time()
        total_tco2 = fetch_and_sum(
            token=token,
            acc_id=acc_id,
            spec_ids=spec_ids,
            psize=int(psize),
            lang=lang,
            no_default_fields=bool(no_default_fields),
            proj=proj,
            groups=groups,
            lastloc=bool(lastloc),
            lng_unit=lng_unit,
            lng_density=float(lng_density),
        )
        t1 = time.time()
        fetch_ms = int((t1 - t0) * 1000)

        # Format like your app—two decimals normal + last decimal emphasized
        s = f"{total_tco2:.3f}"  # e.g. 21558.489
        whole, frac = s.split(".")
        first_two, last = frac[:2], frac[2:]

        # Simple styling (white int, light-green first two, bigger green last)
        html = f"""
        <div style="font-family: ui-monospace, Menlo, Consolas, 'SF Mono', monospace;
                    text-align:center; line-height:1; margin-top:0.5rem;">
            <span style="font-size:80px; font-weight:800; color:#FFFFFF;">{int(whole):,}</span>
            <span style="font-size:80px; font-weight:800; color:#9FF7C6;">.{first_two}</span>
            <span style="font-size:96px; font-weight:900; color:#39D353;">{last}</span>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)

        # Tiny status line
        st.write(f"Fetched in **{fetch_ms} ms** · psize={psize} · lastloc={'on' if lastloc else 'off'}")

    except requests.HTTPError as e:
        st.error(f"HTTP ERROR: {e.response.status_code} {e.response.text[:300]}")
    except requests.RequestException as e:
        st.error(f"NETWORK ERROR: {e}")
    except Exception as e:
        st.error(f"ERROR: {e}")
