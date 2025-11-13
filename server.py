import os
import time
import threading
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, jsonify, render_template

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

# ==== configuration ====
INTANGLES_TOKEN = os.getenv("INTANGLES_TOKEN", "")
ACC_ID      = "962759605811675136"
SPEC_IDS    = "966986020958502912,969208267156750336"
PSIZE       = 300
LANG        = "en"
NO_DEF      = True
PROJ        = "total_fuel_consumed"
GROUPS      = ""
LASTLOC     = True
LNG_UNIT    = "kg"
LNG_DENSITY = 0.45
UI_OFFSET   = 0.0    # tons added before display

# cache shared by all requests
LAST_VALUE: Optional[float] = None
LAST_TS    = 0.0
CACHE_TTL  = 1.0      # seconds between background refreshes

# flag to start background thread only once
background_started = False
background_lock = threading.Lock()

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
            r = s.get(url, params=params, headers=headers, timeout=45)
            r.raise_for_status()
            payload = r.json()
            rows = list(iter_payload_rows(payload))
            if not rows:
                break
            if fuel_key is None:
                fuel_key = detect_fuel_key(rows[:10])
                if not fuel_key:
                    raise RuntimeError("Fuel field not detected")
            for rw in rows:
                v = get_value_by_dotted(rw, fuel_key)
                if v:
                    total_input += v
            if len(rows) < psize:
                break
            pnum += 1

    total_lng_kg = lng_to_kg(total_input, lng_unit, lng_density)
    total_tco2_saved = (total_lng_kg * SAVINGS_PER_KG) / 1000.0
    return total_tco2_saved

# ==== background updater ====
def background_updater():
    global LAST_VALUE, LAST_TS
    while True:
        if INTANGLES_TOKEN:
            try:
                raw_value = fetch_and_sum(
                    INTANGLES_TOKEN,
                    ACC_ID,
                    SPEC_IDS,
                    PSIZE,
                    LANG,
                    NO_DEF,
                    PROJ,
                    GROUPS,
                    LASTLOC,
                    LNG_UNIT,
                    LNG_DENSITY,
                )
                val = raw_value + UI_OFFSET
                LAST_VALUE = val
                LAST_TS = time.time()
                print("Updated LAST_VALUE:", LAST_VALUE)
            except Exception as e:
                print("Intangles API error in background updater:", repr(e))
        else:
            print("INTANGLES_TOKEN not set; background updater idle")
        time.sleep(CACHE_TTL)

app = Flask(__name__)  # looks for templates/index.html by default

@app.before_request
def ensure_background_thread():
    global background_started
    if not background_started:
        with background_lock:
            if not background_started:
                t = threading.Thread(target=background_updater, daemon=True)
                t.start()
                background_started = True
                print("Background updater thread started")

# ==== routes ====
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/value")
def get_value():
    # Always fast, just returns cached value
    if LAST_VALUE is None:
        # Background thread hasn't fetched anything yet
        return jsonify({"error": "Value not ready yet"}), 503
    return jsonify({"value": round(LAST_VALUE, 3)})

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=True)
