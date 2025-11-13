# app.py
import os
import time
import math
import threading
from dataclasses import dataclass
from typing import Optional, Tuple

import requests
import streamlit as st

# ---------- Page / Theme ----------
st.set_page_config(page_title="Blue Energy Motors – Live CO₂", layout="wide")
# Global styles (dark bg, light blue headers, tabular digits, etc.)
st.markdown("""
<style>
:root {
  --bg: #000000;
  --white: #FFFFFF;
  --lightBlue: #DAFFFF;
  --lightGreen: #9FF7C6;
  --green: #39D353;
  --ghost: rgba(255,255,255,0.03);
  --digit-font-size: 13vw;      /* scales with viewport width */
  --digit-last-scale: 1.22;     /* last decimal is bigger */
  --label-font-size: 1.4rem;
}

/* Reset / page */
html, body, [data-testid="stAppViewContainer"] {
  background: var(--bg) !important;
  color: var(--white) !important;
}

/* Top banner */
.bem-banner {
  width: 100%;
  border-top: 3px solid var(--lightBlue);
  border-bottom: 3px solid var(--lightBlue);
  margin: .25rem 0 1rem 0;
  text-align: center;
  color: var(--lightBlue);
  font-weight: 800;
  font-size: clamp(24px, 3vw, 40px);
  letter-spacing: .5px;
  padding: .5rem 0;
}

/* Number block */
.num-wrap {
  position: relative;
  width: 100%;
  text-align: center;
  line-height: 1;
  margin: 0;
  padding: 0;
}

/* Use a font with tabular numbers (most browsers: monospace has it by default).
   font-variant-numeric ensures equal digit widths, critical for alignment. */
.num-inner {
  display: inline-block;
  position: relative;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-variant-numeric: tabular-nums;
}

/* The ghost digits behind (very faint) */
.num-ghost {
  position: absolute;
  left: 0;
  right: 0;
  top: 0;
  opacity: 1;            /* color already has low alpha */
  color: var(--ghost);
  pointer-events: none;
  user-select: none;
}

/* Foreground styled number */
.num-fg {
  position: relative;
  color: var(--white);
}

/* Sizing of each part (responsive) */
.num-int, .num-dot, .num-dec2 {
  font-size: min(var(--digit-font-size), 18vh);
  font-weight: 800;
}
.num-last {
  font-size: calc(min(var(--digit-font-size), 18vh) * var(--digit-last-scale));
  font-weight: 900;
}

/* Colors for parts */
.num-dec2 { color: var(--lightGreen); }
.num-last { color: var(--green); }

/* Label line under the number */
.label-row {
  margin-top: .5rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-variant-numeric: tabular-nums;
  white-space: pre;      /* preserve spaces */
  text-align: center;
  line-height: 1.1;
  min-height: 2.2rem;
}
.label-tons { color: rgba(255,255,255,0.80); font-size: var(--label-font-size); font-weight: 600; }
.label-kg   { color: rgba(255,255,255,0.60); font-size: var(--label-font-size); font-weight: 600; }

/* Bottom tagline */
.tagline {
  color: var(--lightBlue);
  text-align: center;
  font-size: clamp(18px, 2.2vw, 32px);
  margin: .5rem 0 1rem 0;
}
</style>
""", unsafe_allow_html=True)

# ---------- Config ----------
# If you have a live endpoint, set these (or leave empty to use a simple local counter).
INTANGLES_BASE_URL = os.getenv("INTANGLES_BASE_URL", "").strip()   # e.g. https://api.example.com
INTANGLES_TOKEN = os.getenv("INTANGLES_TOKEN", "").strip()
VEHICLE_ID = os.getenv("VEHICLE_ID", "").strip()

# Poll interval (seconds). Your Android port used 0.5s for “snappy” feel.
POLL_SECS = 0.5

# Add +1000 to the displayed number, as in your Compose UI.
DISPLAY_OFFSET = 1000.0

# ---------- Data Model ----------
@dataclass
class Tco2Data:
    value_tons: float
    ts: float

def fetch_from_api() -> Optional[float]:
    """Fetch latest tCO2 value (tons) from an API. Return None on failure."""
    if not INTANGLES_BASE_URL or not INTANGLES_TOKEN or not VEHICLE_ID:
        return None
    url = f"{INTANGLES_BASE_URL.rstrip('/')}/v1/vehicles/{VEHICLE_ID}/tco2"
    try:
        resp = requests.get(url, headers={"Authorization": f"Bearer {INTANGLES_TOKEN}"}, timeout=5)
        resp.raise_for_status()
        j = resp.json()
        # Expect server returns {"tco2": <float in tons>}
        val = float(j.get("tco2"))
        return val
    except Exception:
        return None

def get_latest_value(prev: Optional[Tco2Data]) -> Tco2Data:
    """
    Return the most recent tCO2 (tons) and timestamp.
    - Tries API if configured; falls back to a local smooth counter.
    - Never decreases (like your 'lastLoc' guard).
    """
    now = time.time()
    api_val = fetch_from_api()
    if api_val is not None:
        if prev is None:
            return Tco2Data(api_val, now)
        return Tco2Data(max(prev.value_tons, api_val), now)

    # Fallback: local smooth growth ~simulating live increments (e.g., 0.003 t every second)
    rate_tons_per_sec = 0.003
    base = prev.value_tons if prev else 0.0
    dt = (now - (prev.ts if prev else now))
    return Tco2Data(base + rate_tons_per_sec * dt, now)

# ---------- Formatting Utilities ----------
def split_parts(value: float, fraction_digits: int = 3) -> Tuple[str, str, str]:
    """
    Return (int_part, first_two_decimals, last_decimal) as strings.
    Leading zeros in integer part are trimmed except we keep "0".
    """
    s = f"{value:.{fraction_digits}f}"
    dot = s.find(".")
    raw_int = s[:dot] if dot >= 0 else s
    trimmed = raw_int.lstrip("0") or "0"

    if dot >= 0:
        frac = s[dot + 1:]
    else:
        frac = "0" * fraction_digits

    if len(frac) < fraction_digits:
        frac = (frac + "0" * fraction_digits)[:fraction_digits]

    first_two = frac[:2]
    last = frac[2] if len(frac) >= 3 else "0"
    return trimmed, first_two, last

def ghost_template(int_len: int) -> Tuple[str, str, str]:
    """
    Build ghost strings sized to the current number geometry using '8's.
    Returns (ghost_int, ghost_dec2, ghost_last).
    """
    return ("8" * int_len, "88", "8")

def build_label_lines(int_len: int, dec2_len: int = 2) -> Tuple[str, str]:
    """
    Build two label strings using mono/tabular font spacing, centered overall but aligned
    under the number geometry:
    - 'TONS' ends under the last integer digit (right-aligned to integer part)
    - 'KG' starts exactly under the last decimal digit (left-aligned)
    We return (tons_line, kg_line).
    """
    tons = "TONS"
    kg = "KG"

    # TONS: make it end at int_len
    spaces_before_tons = max(0, int_len - len(tons))
    tons_line = " " * spaces_before_tons + tons

    # KG: position left edge under the last decimal digit:
    # positions: [ints] + dot(1) + firstTwo(2) => start of last decimal is index int_len + 1 + 2
    kg_start_col = int_len + 1 + dec2_len
    kg_line = " " * kg_start_col + kg

    return tons_line, kg_line

# ---------- Session State ----------
if "latest" not in st.session_state:
    st.session_state.latest = None  # type: Optional[Tco2Data]

# ---------- UI ----------
st.markdown('<div class="bem-banner"> Blue Energy Motors : Real-Time CO₂ Saved </div>', unsafe_allow_html=True)

placeholder = st.empty()

# Small “runtime” note with options
with st.sidebar:
    st.markdown("### Live Options")
    st.write("- Poll every **0.5s**")
    st.write("- Add **+1000** tons in UI (to mimic your Android build)")
    st.divider()
    st.write("Set `INTANGLES_BASE_URL`, `INTANGLES_TOKEN`, `VEHICLE_ID` env vars to read real data.")
    st.write("Without them, this uses a smooth local counter.")

# ---------- Live Loop ----------
# Streamlit allows a short loop with time.sleep within a single run using a placeholder.
# This keeps the UI very responsive without custom components.
for _ in range(600000):  # effectively “forever” in a long session
    prev = st.session_state.latest
    cur = get_latest_value(prev)
    st.session_state.latest = cur

    # UI offset like your Compose code
    to_display = max(0.0, cur.value_tons) + DISPLAY_OFFSET

    # Split parts
    int_part, dec2, last_dec = split_parts(to_display, 3)
    int_len = len(int_part)

    # Ghost strings sized to the current geometry
    g_int, g_dec2, g_last = ghost_template(int_len)

    # Labels aligned under number geometry
    tons_line, kg_line = build_label_lines(int_len=int_len, dec2_len=2)

    # Render
    html = f"""
    <div class="num-wrap">
      <div class="num-inner">
        <!-- Ghost layer -->
        <div class="num-ghost" aria-hidden="true">
          <span class="num-int">{g_int}</span><span class="num-dot">. </span><span class="num-dec2">{g_dec2}</span><span class="num-last">{g_last}</span>
        </div>
        <!-- Foreground number -->
        <div class="num-fg">
          <span class="num-int">{int_part}</span><span class="num-dot">.</span><span class="num-dec2">{dec2}</span><span class="num-last">{last_dec}</span>
        </div>
      </div>
    </div>

    <div class="label-row">
      <div class="label-tons">{tons_line}</div>
      <div class="label-kg">{kg_line}</div>
    </div>

    <div class="tagline">Forest🌳on Wheels — Driving a Greener Tomorrow 🌍🌿</div>
    """

    with placeholder.container():
        st.markdown(html, unsafe_allow_html=True)

    time.sleep(POLL_SECS)
