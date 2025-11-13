#!/usr/bin/env python3
# streamlit run carbonclock.py
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
import streamlit as st

# ─────────────────────────────── Constants ───────────────────────────────
BASE_URL = "https://apis.intangles.com"
PATH = "/vehicle/fuel_consumed"

# kg CO₂ saved per kg LNG (to match the platform)
SAVINGS_PER_KG = 0.926

REFERER = "https://bemblueedge.intangles.com/"
ORIGIN = "https://bemblueedge.intangles.com"

# Preferred keys in the payload
PREFERRED_KEYS = [
    "total_fuel_consumed",
    "data.total_fuel_consumed",
    "fuel_consumed",
    "total_fuel",
    "fuel_total",
    "fuel",
]


# ────────────────────────────── HTTP helpers ─────────────────────────────
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


# ───────────────────────────── Payload helpers ───────────────────────────
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
        # fallback: maybe payload itself is a row dict
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
            # not strictly dotted under dict; try deep-scan once
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
    timeout: float = 45.0,
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
            resp = s.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            payload = resp.json()

            rows = list(iter_payload_rows(payload))
            if not rows:
                break

            if fuel_key is None:
                sample = rows[:10]
                detected = detect_fuel_key(sample)
                if not detected:
                    raise RuntimeError(
                        "Could not detect a fuel field from API payload."
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


# ─────────────────────────── Auto-refresh helper ─────────────────────────
def auto_refresh(seconds: float, key: str = "poll_timer"):
    """
    Version-agnostic auto refresh. Uses st.autorefresh if available,
    otherwise a SessionState timer + experimental_rerun.
    """
    try:
        # Available in newer Streamlit versions
        st.autorefresh(interval=int(seconds * 1000), key=key)
        return
    except AttributeError:
        pass

    tkey = f"{key}__last"
    now = time.time()
    last = st.session_state.get(tkey)
    if last is None:
        st.session_state[tkey] = now
    elif (now - last) >= seconds:
        st.session_state[tkey] = now
        st.experimental_rerun()


# ─────────────────────────────── UI / App ────────────────────────────────
st.set_page_config(page_title="tCO₂ Live — Intangles", page_icon="🌍", layout="wide")

st.markdown(
    """
    <style>
      .big { font-size: 72px; font-weight: 800; letter-spacing: 1px; }
      .sub { font-size: 22px; opacity: 0.85; }
      .num { font-size: 96px; font-weight: 900; }
      .num .int { color: #FFFFFF; }
      .num .dec2 { color: #9FF7C6; }  /* light green for first two decimals */
      .num .dec3 { color: #39D353; font-size: 1.22em; } /* big green last digit */
      .brand { color: #DAFFFF; font-weight: 800; font-size: 28px; }
      .banner { color: #DAFFFF; font-size: 22px; }
      .mono { font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)

# Sidebar controls
with st.sidebar:
    st.header("Settings")
    token = (
        st.secrets.get("INTANGLES_TOKEN")
        if "INTANGLES_TOKEN" in st.secrets
        else os.getenv("INTANGLES_TOKEN", "")
    )
    token = st.text_input("Intangles token", token, type="password")
    acc_id = st.text_input("Account ID", "962759605811675136")
    spec_ids = st.text_input("Spec IDs (comma-separated)", "966986020958502912,969208267156750336")
    psize = st.number_input("Page size (psize)", min_value=50, max_value=1000, value=300, step=50)
    lastloc = st.checkbox("lastloc", value=True)
    lng_unit = st.selectbox("LNG unit", options=["kg", "L"], index=0)
    lng_density = st.number_input("LNG density (kg/L)", min_value=0.1, max_value=1.0, value=0.45, step=0.01)
    refresh = st.number_input("Refresh every (seconds)", min_value=0.5, max_value=30.0, value=2.0, step=0.5)
    add_offset = st.checkbox("Add +1000 (UI only)", value=False)

# Header / banner
colA, colB = st.columns([1, 3], gap="large")
with colA:
    st.markdown('<div class="brand">Blue Energy Motors</div>', unsafe_allow_html=True)
with colB:
    st.markdown('<div class="banner">Real-Time CO₂ Saved</div>', unsafe_allow_html=True)

st.markdown("---")

# Poll + compute
error_box = st.empty()
value_box = st.empty()
detail_box = st.empty()

def format_big_number(v: float) -> str:
    # Format with 3 decimals, then split: int part, first two decimals, last decimal
    s = f"{v:,.3f}"
    # Example: "21,558.489"
    if "." in s:
        ip, dp = s.split(".")
    else:
        ip, dp = s, "000"
    dp = (dp + "000")[:3]  # safety
    return f'<span class="num mono"><span class="int">{ip}</span>.<span class="dec2">{dp[:2]}</span><span class="dec3">{dp[2]}</span></span>'

try:
    if not token:
        raise RuntimeError("Missing token. Provide it in the sidebar (or set INTANGLES_TOKEN in secrets/env).")

    total_tco2 = fetch_and_sum(
        token=token,
        acc_id=acc_id,
        spec_ids=spec_ids,
        psize=int(psize),
        lang="en",
        no_default_fields=True,
        proj="total_fuel_consumed",
        groups="",
        lastloc=bool(lastloc),
        lng_unit=lng_unit,
        lng_density=float(lng_density),
    )

    if add_offset:
        total_tco2 += 1000.0

    # Big number
    html_num = format_big_number(total_tco2)
    value_box.markdown(html_num, unsafe_allow_html=True)

    # Subtext
    detail_box.markdown(
        f'<div class="sub">Live from Intangles API ✅<br/>Total tCO₂ saved (tons)</div>',
        unsafe_allow_html=True,
    )

except requests.HTTPError as e:
    error_box.error(f"HTTP {e.response.status_code}: {e.response.text[:300]}")
except requests.RequestException as e:
    error_box.error(f"Network error: {e}")
except Exception as e:
    error_box.error(f"Error: {e}")

# Auto refresh (works with any Streamlit version)
auto_refresh(float(refresh), key="poll_timer")
