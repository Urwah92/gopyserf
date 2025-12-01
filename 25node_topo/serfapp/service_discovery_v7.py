#!/usr/bin/env python3
"""
multi_stage_hilbert_router_rtt_then_hilbert_http_loop.py

Changes:
- Excludes any candidates that contain NaN in the output fields.
- Loads geometry from --geom-url exactly once at startup (reused each loop).
- get_lan_members(): auto-retries ./serf members with exponential backoff.
- If locals unavailable after retries, the cycle is skipped (no CH queries).
"""

import argparse, json, math, socket, subprocess, threading, time
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hilbertcurve.hilbertcurve import HilbertCurve

DEFAULT_SERF_RPC = "127.0.0.1:7373"
DEFAULT_TIMEOUT_S = 8
NET_P_BITS = 14
NODE_JSON_PATH = "/opt/serfapp/node.json"

# ---------------------------------- I/O ----------------------------------
def load_geometry(url: str, timeout: int = 5) -> List[Dict[str, Any]]:
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)

def extract_rtts(nodes: List[dict]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for n in nodes:
        nm = n.get("name")
        r = n.get("rtts") or {}
        if isinstance(nm, str) and isinstance(r, dict):
            clean = {}
            for k, v in r.items():
                try:
                    x = float(v)
                    if math.isfinite(x):
                        clean[str(k)] = x
                except Exception:
                    pass
            out[nm] = clean
    return out

def _to_int(v) -> int:
    try:
        if v is None:
            return 0
        s = str(v).strip().replace(",", "")
        return int(float(s))
    except Exception:
        return 0

def _to_float(v) -> float:
    try:
        if v is None:
            return float("nan")
        return float(str(v).strip())
    except Exception:
        return float("nan")

# --------- Local meta (read node name from node.json) ----------
def _read_node_json(path: str = NODE_JSON_PATH) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return ""

def _node_name_from_nodejson_or_hostname() -> str:
    meta = _read_node_json()
    name = meta.get("node_name") or meta.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    hn = _hostname()
    if hn:
        return hn
    raise SystemExit(f"Cannot determine node name: {NODE_JSON_PATH} missing 'node_name' and hostname lookup failed.")

# ------------------- Local resources from Serf (LAN) ---------------------
def get_lan_members(rpc_addr: str) -> pd.DataFrame:
    """
    Returns local members (no -wan) with ip + resource tags.
    Auto-retries `./serf members` with exponential backoff.
    """
    cols = [
        "name","ip","cpu","ram","storage","gpu",
        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
    ]

    max_attempts = 6          # total tries
    base_ms = 200             # initial backoff in ms
    cap_s = 2.0               # max sleep per backoff

    last_err = None
    for attempt in range(max_attempts):
        try:
            res = subprocess.run(
                ["./serf","members",f"-rpc-addr={rpc_addr}","-format=json"],
                capture_output=True, text=True
            )
            if res.returncode == 0:
                try:
                    data = json.loads(res.stdout or "{}")
                except Exception as je:
                    last_err = f"json decode error: {je}"
                    raise

                members = data.get("members") or data.get("Members") or []
                rows = []
                for m in members:
                    if not isinstance(m, dict):
                        continue
                    name = m.get("name") or m.get("Name")
                    if not name or "-wan" in str(name).lower():
                        continue
                    tags = m.get("tags") or m.get("Tags") or {}
                    if not isinstance(tags, dict):
                        tags = {}

                    ip = tags.get("ip") or m.get("addr") or m.get("Addr") or ""
                    if isinstance(ip, str) and ":" in ip:
                        ip = ip.split(":", 1)[0]

                    rows.append({
                        "name": str(name),
                        "ip": ip,
                        "cpu": _to_int(tags.get("cpu")),
                        "ram": _to_float(tags.get("ram")),
                        "storage": _to_int(tags.get("storage")),
                        "gpu": _to_int(tags.get("gpu")),
                        "price_per_cpu": _to_float(tags.get("price_per_cpu")),
                        "price_per_ram": _to_float(tags.get("price_per_ram")),
                        "price_per_storage": _to_float(tags.get("price_per_storage")),
                        "price_per_gpu": _to_float(tags.get("price_per_gpu")),
                        "score_per_cpu": _to_float(tags.get("score_per_cpu")),
                        "score_per_ram": _to_float(tags.get("score_per_ram")),
                        "score_per_storage": _to_float(tags.get("score_per_storage")),
                        "score_per_gpu": _to_float(tags.get("score_per_gpu")),
                    })
                return pd.DataFrame(rows, columns=cols)

            last_err = (res.stderr or "").strip() or f"exit={res.returncode}"
            print(f"[serf members] attempt {attempt+1}/{max_attempts} failed: {last_err}")

        except Exception as e:
            last_err = str(e)
            print(f"[serf members] attempt {attempt+1}/{max_attempts} raised: {last_err}")

        # backoff before next try
        sleep_s = min((base_ms/1000.0) * (2 ** attempt), cap_s)
        sleep_s += 0.001 * (attempt + 1)  # jitter
        time.sleep(sleep_s)

    print(f"[serf members] giving up after {max_attempts} attempts: {last_err}")
    return pd.DataFrame(columns=cols)

# --------------------- CH request (wanted_names) -------------------------
def _print_names(title: str, names: List[str]):
    if not names:
        print(f"{title}: (none)")
    else:
        print(f"{title} ({len(names)}): " + ", ".join(names))

def ask_cluster_head_for_remote(
    min_cpu:int, min_ram:int, min_storage:int, min_gpu:int,
    wanted_names: List[str], rpc_addr:str, timeout_s:int,
    budget_cpu:float, budget_ram:float, budget_storage:float, budget_gpu:float,
    min_sc_cpu:float, min_sc_ram:float, min_sc_storage:float, min_sc_gpu:float
) -> pd.DataFrame:
    cols = [
        "name","ip","cpu","ram","storage","gpu",
        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
    ]
    if not wanted_names:
        return pd.DataFrame(columns=cols)

    payload = {
        **({"min_cpu":min_cpu} if min_cpu>0 else {}),
        **({"min_ram":min_ram} if min_ram>0 else {}),
        **({"min_storage":min_storage} if min_storage>0 else {}),
        **({"min_gpu":min_gpu} if min_gpu>0 else {}),
        **({"budget_per_cpu":budget_cpu} if budget_cpu>0 else {}),
        **({"budget_per_ram":budget_ram} if budget_ram>0 else {}),
        **({"budget_per_storage":budget_storage} if budget_storage>0 else {}),
        **({"budget_per_gpu":budget_gpu} if budget_gpu>0 else {}),
        **({"min_score_per_cpu":min_sc_cpu} if min_sc_cpu>0 else {}),
        **({"min_score_per_ram":min_sc_ram} if min_sc_ram>0 else {}),
        **({"min_score_per_storage":min_sc_storage} if min_sc_storage>0 else {}),
        **({"min_score_per_gpu":min_sc_gpu} if min_sc_gpu>0 else {}),
        "request_id": "TRACE-HILBERT",
        "wanted_names": wanted_names,
    }
    _print_names("→ CH wanted_names", wanted_names)
    cmd = ["./serf","query", f"-rpc-addr={rpc_addr}", f"-timeout={timeout_s}s",
           "-format=json", "ch.ask-remote-res", json.dumps(payload, separators=(",",":"))]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return parse_ch_answer(res.stdout, set(wanted_names))
    except subprocess.CalledProcessError as e:
        print(f"[CH] serf query failed: {e}")
        return pd.DataFrame(columns=cols)

def parse_ch_answer(text: str, allow: set) -> pd.DataFrame:
    cols = [
        "name","ip","cpu","ram","storage","gpu",
        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
    ]
    try:
        data = json.loads(text)
    except Exception:
        return pd.DataFrame(columns=cols)

    nodes: List[Dict[str, Any]] = []
    responses = data.get("Responses") or {}
    for _from, payload in responses.items():
        try:
            inner = payload if isinstance(payload, (list, dict)) else json.loads(payload)
        except Exception:
            continue

        if isinstance(inner, list):
            arr = inner
        elif isinstance(inner, dict):
            arr = inner.get("nodes") or inner.get("Nodes") or []
            if not isinstance(arr, list):
                arr = []
        else:
            arr = []

        for rec in arr:
            if not isinstance(rec, dict):
                continue
            nm = str(rec.get("name") or rec.get("Name") or "")
            if not nm or (allow and nm not in allow):
                continue
            nodes.append({
                "name": nm,
                "ip": str(rec.get("ip") or rec.get("IP") or ""),
                "cpu": _to_int(rec.get("cpu") or rec.get("CPU")),
                "ram": _to_float(rec.get("ram") or rec.get("RAM")),
                "storage": _to_int(rec.get("storage") or rec.get("Storage")),
                "gpu": _to_int(rec.get("gpu") or rec.get("GPU")),
                "price_per_cpu": _to_float(rec.get("price_per_cpu") or rec.get("PricePerCPU")),
                "price_per_ram": _to_float(rec.get("price_per_ram") or rec.get("PricePerRAM")),
                "price_per_storage": _to_float(rec.get("price_per_storage") or rec.get("PricePerStorage")),
                "price_per_gpu": _to_float(rec.get("price_per_gpu") or rec.get("PricePerGPU")),
                "score_per_cpu": _to_float(rec.get("score_per_cpu") or rec.get("ScorePerCPU")),
                "score_per_ram": _to_float(rec.get("score_per_ram") or rec.get("ScorePerRAM")),
                "score_per_storage": _to_float(rec.get("score_per_storage") or rec.get("ScorePerStorage")),
                "score_per_gpu": _to_float(rec.get("score_per_gpu") or rec.get("ScorePerGPU")),
            })

    if not nodes:
        return pd.DataFrame(columns=cols)

    seen, uniq = set(), []
    for r in nodes:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        uniq.append(r)
    return pd.DataFrame(uniq, columns=cols)

# -------------------------------- Hilbert --------------------------------
def minmax_norm_to_bits(values: np.ndarray, p_bits: int):
    values = np.asarray(values, dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    lo = values.min(axis=0)
    hi = values.max(axis=0)
    span = np.where(hi > lo, hi - lo, 1.0)
    scaled = (values - lo) / span
    return np.round(scaled * ((2 ** p_bits) - 1)).astype(int), lo, hi

class HilbertIndex:
    def __init__(self, nodes: List[dict], p_bits: int = NET_P_BITS):
        self.nodes = [n for n in nodes
                      if isinstance(n.get("coordinate"), dict)
                      and isinstance(n["coordinate"].get("Vec"), list)
                      and isinstance(n.get("name"), str)]
        self.names = [n["name"] for n in self.nodes]
        if not self.names:
            raise SystemExit("no nodes with coordinate.Vec found in geometry")

        geom = np.vstack([np.array(n["coordinate"]["Vec"], dtype=float) for n in self.nodes])
        self.norm, _, _ = minmax_norm_to_bits(geom, p_bits)
        self.H = HilbertCurve(p=p_bits, n=self.norm.shape[1])
        self.h_raw = [int(self.H.distance_from_point(self.norm[i].tolist()))
                      for i in range(len(self.names))]
        self.df = (pd.DataFrame({"name": self.names, "h_raw": self.h_raw})
                     .sort_values("h_raw", kind="mergesort")
                     .reset_index(drop=True))
        self.idx = {nm: i for i, nm in self.df["name"].items()}

    def h(self, name: str) -> Optional[int]:
        if name not in self.idx:
            return None
        return int(self.df.at[self.idx[name], "h_raw"])

    def names_in_raw_window(self, query: str, delta_raw: int) -> List[str]:
        if query not in self.idx:
            return []
        qh = float(self.h(query))
        d = max(0.0, float(delta_raw))
        lo, hi = qh - d, qh + d
        mask = (self.df["h_raw"].astype(float) >= lo) & (self.df["h_raw"].astype(float) <= hi)
        out = self.df.loc[mask, "name"].tolist()
        return [n for n in out if n != query and "-wan" not in n.lower()]

# --------------------------- Filtering & cleaning -------------------------
def _nan_to_inf(series: pd.Series) -> pd.Series:
    return series.where(~series.isna(), float("inf"))

def _nan_to_zero(series: pd.Series) -> pd.Series:
    return series.where(~series.isna(), 0.0)

# Columns that must be non-NaN in outputs
REQUIRED_COLS = [
    "ip","cpu","ram","storage","gpu",
    "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
    "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
]

def drop_nan_members(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    # Ensure all required columns exist
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = np.nan
    # Drop any row with NaN in required columns
    return df.dropna(subset=REQUIRED_COLS, how="any").copy()

def filter_by_resources(
    df: pd.DataFrame, min_cpu:int, min_ram:float, min_storage:int, min_gpu:int,
    budget_cpu:float=0.0, budget_ram:float=0.0, budget_storage:float=0.0, budget_gpu:float=0.0,
    min_sc_cpu:float=0.0, min_sc_ram:float=0.0, min_sc_storage:float=0.0, min_sc_gpu:float=0.0
) -> pd.DataFrame:
    if df.empty:
        return df
    x = df.copy()

    ppc  = _nan_to_inf(x.get("price_per_cpu",      pd.Series(index=x.index, dtype=float)))
    ppr  = _nan_to_inf(x.get("price_per_ram",      pd.Series(index=x.index, dtype=float)))
    ppst = _nan_to_inf(x.get("price_per_storage",  pd.Series(index=x.index, dtype=float)))
    ppg  = _nan_to_inf(x.get("price_per_gpu",      pd.Series(index=x.index, dtype=float)))

    sc_cpu  = _nan_to_zero(x.get("score_per_cpu",      pd.Series(index=x.index, dtype=float)))
    sc_ram  = _nan_to_zero(x.get("score_per_ram",      pd.Series(index=x.index, dtype=float)))
    sc_sto  = _nan_to_zero(x.get("score_per_storage",  pd.Series(index=x.index, dtype=float)))
    sc_gpu  = _nan_to_zero(x.get("score_per_gpu",      pd.Series(index=x.index, dtype=float)))

    mask = (
        ((min_cpu<=0)     | (x["cpu"]     >= min_cpu)) &
        ((min_ram<=0)     | (x["ram"]     >= min_ram)) &
        ((min_storage<=0) | (x["storage"] >= min_storage)) &
        ((min_gpu<=0)     | (x["gpu"]     >= min_gpu)) &
        ((budget_cpu<=0)      | (ppc  <= budget_cpu)) &
        ((budget_ram<=0)      | (ppr  <= budget_ram)) &
        ((budget_storage<=0)  | (ppst <= budget_storage)) &
        ((budget_gpu<=0)      | (ppg  <= budget_gpu)) &
        ((min_sc_cpu<=0)      | (sc_cpu >= min_sc_cpu)) &
        ((min_sc_ram<=0)      | (sc_ram >= min_sc_ram)) &
        ((min_sc_storage<=0)  | (sc_sto >= min_sc_storage)) &
        ((min_sc_gpu<=0)      | (sc_gpu >= min_sc_gpu))
    )
    return x.loc[mask].copy()

def sort_candidates(x: pd.DataFrame, key: str) -> pd.DataFrame:
    if x.empty or key == "none":
        return x
    ascending = True
    if key in {"cpu","ram","storage","gpu","score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"}:
        ascending = False
    return x.sort_values(by=[key,"name"], ascending=[ascending, True])

# ------------------------------ HTTP server ------------------------------
def start_live_http_server(state, host: str, port: int, path: str):
    import http.server, socketserver, json as _json, math as _m

    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        if isinstance(obj, float):
            if _m.isnan(obj) or _m.isinf(obj):
                return None
        return obj

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == path:
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store")
                self.end_headers()
                with state["lock"]:
                    payload = state.get("payload", {"scope":"none","results":[]})
                    self.wfile.write(_json.dumps(clean(payload), separators=(",",":")).encode("utf-8"))
            elif self.path == "/healthz":
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            else:
                self.send_response(404); self.end_headers()
        def log_message(self, fmt, *args): return

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = ThreadingHTTPServer((host, port), Handler)
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval":0.5}, daemon=True)
    t.start()
    print(f"[http] live endpoint at http://{host}:{port}{path}")
    return httpd

# ------------------------------ Buyer loader -----------------------------
def load_buyer(url: str, timeout: int = 5) -> dict:
    import urllib.request, urllib.error
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        print(f"[buyer] HTTP error {e.code} from {url}")
    except Exception as e:
        print(f"[buyer] fetch error from {url}: {e}")
    return {}

def apply_buyer_overrides(args, ap):
    if not args.buyer_url:
        return
    buyer = load_buyer(args.buyer_url, timeout=5)
    res = buyer.get("resources") or {}

    def _get(field, key, dv=0.0):
        try:
            v = res.get(field, {}).get(key, None)
            return dv if v in (None, "") else float(v)
        except Exception:
            return dv

    # Reset floors to CLI defaults each cycle so values don't ratchet
    args.min_cpu = ap.get_default("min_cpu")
    args.min_ram = ap.get_default("min_ram")
    args.min_storage = ap.get_default("min_storage")
    args.min_gpu = ap.get_default("min_gpu")
    args.budget_per_cpu = ap.get_default("budget_per_cpu")
    args.budget_per_ram = ap.get_default("budget_per_ram")
    args.budget_per_storage = ap.get_default("budget_per_storage")
    args.budget_per_gpu = ap.get_default("budget_per_gpu")
    args.min_score_per_cpu = ap.get_default("min_score_per_cpu")
    args.min_score_per_ram = ap.get_default("min_score_per_ram")
    args.min_score_per_storage = ap.get_default("min_score_per_storage")
    args.min_score_per_gpu = ap.get_default("min_score_per_gpu")

    # Demands -> minimums
    args.min_cpu     = max(args.min_cpu,     int(_get("vcpu","demand_per_unit",0)))
    args.min_ram     = max(args.min_ram,     float(_get("ram","demand_per_unit",0)))
    args.min_storage = max(args.min_storage, int(_get("storage","demand_per_unit",0)))
    args.min_gpu     = max(args.min_gpu,     int(_get("vgpu","demand_per_unit",0)))

    # Per-unit budgets (only override if >0)
    b_cpu = _get("vcpu","budget",0.0)
    if b_cpu > 0:
        args.budget_per_cpu = b_cpu
    b_ram = _get("ram","budget",0.0)
    if b_ram > 0:
        args.budget_per_ram = b_ram
    b_sto = _get("storage","budget",0.0)
    if b_sto > 0:
        args.budget_per_storage = b_sto
    b_gpu = _get("vgpu","budget",0.0)
    if b_gpu > 0:
        args.budget_per_gpu = b_gpu

    # Per-unit score minimums (only override if >0)
    sc_cpu = _get("vcpu","score",0.0)
    if sc_cpu > 0:
        args.min_score_per_cpu = sc_cpu
    sc_ram = _get("ram","score",0.0)
    if sc_ram > 0:
        args.min_score_per_ram = sc_ram
    sc_sto = _get("storage","score",0.0)
    if sc_sto > 0:
        args.min_score_per_storage = sc_sto
    sc_gpu = _get("vgpu","score",0.0)
    if sc_gpu > 0:
        args.min_score_per_gpu = sc_gpu

    print(
        f"[buyer] loaded from {args.buyer_url}: "
        f"min(cpu={args.min_cpu}, ram={args.min_ram}, storage={args.min_storage}, gpu={args.min_gpu}); "
        f"budget(cpu={args.budget_per_cpu}, ram={args.budget_per_ram}, storage={args.budget_per_storage}, gpu={args.budget_per_gpu}); "
        f"score_min(cpu={args.min_score_per_cpu}, ram={args.min_score_per_ram}, storage={args.min_score_per_storage}, gpu={args.min_score_per_gpu})"
    )

# ----------------------------- One discovery run -------------------------
def run_once(query_node: str, args, H: HilbertIndex, rtts: Dict[str, Dict[str, float]]) -> dict:
    # LAN members fetched fresh each loop (dynamic tags) with internal retry
    lan_df = get_lan_members(args.rpc_addr)

    # If we couldn’t read locals at all, skip this cycle (avoid CH misfire)
    if lan_df.empty:
        print("[warn] ./serf members unavailable after retries; skipping this cycle (no CH).")
        return {"query": query_node, "scope": "none", "results": []}

    lan_names = set(lan_df["name"].tolist())

    if args.dump_hilbert:
        print("\n=== HILBERT TABLE (sorted by h_raw) ===")
        print(H.df.to_string(index=False))

    # -------- Phase A: RTT slice (sequential: local -> remote) --------
    rtt_map = rtts.get(query_node, {})
    rtt_names = [n for n, r in rtt_map.items()
                 if r is not None and r <= args.rtt_threshold_ms and n in H.idx and n != query_node]

    local_names = [n for n in rtt_names if n in lan_names]
    remote_names = [n for n in rtt_names if n not in lan_names]
    _print_names("• RTT local names", local_names)
    _print_names("• RTT remote names", remote_names)

    b_cpu, b_ram, b_sto, b_gpu = (
        args.budget_per_cpu, args.budget_per_ram, args.budget_per_storage, args.budget_per_gpu
    )
    sc_cpu, sc_ram, sc_sto, sc_gpu = (
        args.min_score_per_cpu, args.min_score_per_ram, args.min_score_per_storage, args.min_score_per_gpu
    )

    # A1) Local first
    if local_names:
        local_view = lan_df[lan_df["name"].isin(local_names)][[
            "name","ip","cpu","ram","storage","gpu",
            "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
            "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
        ]].copy()
        local_view["origin"] = "local"
        local_view = drop_nan_members(local_view)  # <-- remove NaN members
        local_pass = filter_by_resources(
            local_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
            b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
        )
        if not local_pass.empty:
            out = sort_candidates(local_pass, args.sort) if args.sort != "none" else local_pass
            if args.limit > 0:
                out = out.head(args.limit)
            out["rtt_to_query"] = [rtt_map.get(n, float("nan")) for n in out["name"]]
            out = drop_nan_members(out)  # ensure no NaN sneaks in
            if not out.empty:
                payload = {
                    "query": query_node,
                    "scope": "rtt-local",
                    "rtt_threshold_ms": args.rtt_threshold_ms,
                    "results": out.to_dict(orient="records")
                }
                print("\n=== RESULTS (RTT local) ===")
                cols = ["ip","origin","cpu","ram","storage","gpu",
                        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu",
                        "rtt_to_query"]
                print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                return payload

    # A2) Remote via CH (only if locals failed the filters)
    if remote_names:
        remote_view = ask_cluster_head_for_remote(
            args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
            wanted_names=remote_names, rpc_addr=args.rpc_addr, timeout_s=args.timeout_s,
            budget_cpu=b_cpu, budget_ram=b_ram, budget_storage=b_sto, budget_gpu=b_gpu,
            min_sc_cpu=sc_cpu, min_sc_ram=sc_ram, min_sc_storage=sc_sto, min_sc_gpu=sc_gpu
        )
        if not remote_view.empty:
            remote_view["origin"] = "wan"
            remote_view = drop_nan_members(remote_view)  # <-- remove NaN members
            remote_pass = filter_by_resources(
                remote_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
            )
            if not remote_pass.empty:
                out = sort_candidates(remote_pass, args.sort) if args.sort != "none" else remote_pass
                if args.limit > 0:
                    out = out.head(args.limit)
                out["rtt_to_query"] = [rtt_map.get(n, float("nan")) for n in out["name"]]
                out = drop_nan_members(out)
                if not out.empty:
                    payload = {
                        "query": query_node,
                        "scope": "rtt-remote",
                        "rtt_threshold_ms": args.rtt_threshold_ms,
                        "results": out.to_dict(orient="records")
                    }
                    print("\n=== RESULTS (RTT remote via CH) ===")
                    cols = ["ip","origin","cpu","ram","storage","gpu",
                            "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                            "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu",
                            "rtt_to_query"]
                    print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                    return payload

    print("\n[RTT] no matches (or none passed resources). Widening by raw Hilbert Δ as % of span…")

    # -------- Phase B: raw-Hilbert widening (sequential per window) --------
    hmin = int(H.df["h_raw"].min())
    hmax = int(H.df["h_raw"].max())
    span = max(1, hmax - hmin)
    delta0 = max(1, int(args.pct_start * span))
    q = query_node

    for step in range(args.max_steps + 1):
        delta = delta0 * (2 ** step)
        pct = args.pct_start * (2 ** step)
        cand = H.names_in_raw_window(q, delta_raw=int(delta))

        print(f"\n[step {step}] Δ_raw={int(delta)} (~{pct*100:.2f}% of span) -> window size={len(cand)}")
        if not cand:
            continue

        local_names = [n for n in cand if n in lan_names]
        remote_names = [n for n in cand if n not in lan_names]
        _print_names("• window local names", local_names)
        _print_names("• window remote names", remote_names)

        # B1) LOCAL first for this window
        if local_names:
            local_view = lan_df[lan_df["name"].isin(local_names)][[
                "name","ip","cpu","ram","storage","gpu",
                "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
            ]].copy()
            local_view["origin"] = "local"
            local_view = drop_nan_members(local_view)
            local_pass = filter_by_resources(
                local_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
            )
            if not local_pass.empty:
                out = sort_candidates(local_pass, args.sort) if args.sort != "none" else local_pass
                if args.limit > 0:
                    out = out.head(args.limit)
                out = drop_nan_members(out)
                if not out.empty:
                    payload = {
                        "query": q,
                        "scope": "hilbert-local",
                        "step": step,
                        "delta_raw": int(delta),
                        "delta_pct_of_span": pct,
                        "results": out.to_dict(orient="records"),
                    }
                    print("\n=== RESULTS (Hilbert window LOCAL) ===")
                    cols = ["ip","origin","cpu","ram","storage","gpu",
                            "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                            "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"]
                    print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                    return payload

        # B2) REMOTE via CH for this window
        if remote_names:
            remote_view = ask_cluster_head_for_remote(
                args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                wanted_names=remote_names, rpc_addr=args.rpc_addr, timeout_s=args.timeout_s,
                budget_cpu=b_cpu, budget_ram=b_ram, budget_storage=b_sto, budget_gpu=b_gpu,
                min_sc_cpu=sc_cpu, min_sc_ram=sc_ram, min_sc_storage=sc_sto, min_sc_gpu=sc_gpu
            )
            if not remote_view.empty:
                remote_view["origin"] = "wan"
                remote_view = drop_nan_members(remote_view)
                remote_pass = filter_by_resources(
                    remote_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                    b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
                )
                if not remote_pass.empty:
                    out = sort_candidates(remote_pass, args.sort) if args.sort != "none" else remote_pass
                    if args.limit > 0:
                        out = out.head(args.limit)
                    out = drop_nan_members(out)
                    if not out.empty:
                        payload = {
                            "query": q,
                            "scope": "hilbert-remote",
                            "step": step,
                            "delta_raw": int(delta),
                            "delta_pct_of_span": pct,
                            "results": out.to_dict(orient="records"),
                        }
                        print("\n=== RESULTS (Hilbert window REMOTE via CH) ===")
                        cols = ["ip","origin","cpu","ram","storage","gpu",
                                "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                                "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"]
                        print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                        return payload

        print("• no passing nodes in this window; widening…")

    payload = {"query": q, "scope": "none", "results": []}
    print("\n=== RESULTS ===\n(no matches after all steps)")
    return payload

# ---------------------------------- Main ----------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="RTT-first, then raw-Hilbert widening. Local checked first, remote via CH only if locals fail. No merging."
    )
    ap.add_argument("--geom-url", required=True, help="HTTP /cluster-status (name, coordinate.Vec, rtts)")
    ap.add_argument("--rtt-threshold-ms", type=float, required=True, help="RTT cutoff for Phase A")

    # Serf / CH
    ap.add_argument("--rpc-addr", default=DEFAULT_SERF_RPC, help="Serf RPC address (for serf members and CH query)")
    ap.add_argument("--timeout-s", type=int, default=DEFAULT_TIMEOUT_S, help="timeout for CH query")

    # Δ widening config (Hilbert Phase)
    ap.add_argument("--pct-start", type=float, default=0.02, help="initial Δ as fraction of span (e.g., 0.02 = 2%)")
    ap.add_argument("--max-steps", type=int, default=6, help="number of doublings (pct, 2*pct, ...)")

    # Resource thresholds
    ap.add_argument("--min-cpu", type=int, default=0)
    ap.add_argument("--min-ram", type=float, default=0)
    ap.add_argument("--min-storage", type=int, default=0)
    ap.add_argument("--min-gpu", type=int, default=0)

    # Per-unit price budgets (caps)
    ap.add_argument("--budget-per-cpu", type=float, default=0.0)
    ap.add_argument("--budget-per-ram", type=float, default=0.0)
    ap.add_argument("--budget-per-storage", type=float, default=0.0)
    ap.add_argument("--budget-per-gpu", type=float, default=0.0)

    # Score filters (per-unit score minimums)
    ap.add_argument("--min-score-per-cpu", type=float, default=0.0)
    ap.add_argument("--min-score-per-ram", type=float, default=0.0)
    ap.add_argument("--min-score-per-storage", type=float, default=0.0)
    ap.add_argument("--min-score-per-gpu", type=float, default=0.0)

    ap.add_argument("--sort", choices=[
        "none",
        "cpu","ram","storage","gpu",
        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
    ], default="score_per_cpu")
    ap.add_argument("--limit", type=int, default=0)

    # Loop & HTTP
    ap.add_argument("--dump-hilbert", action="store_true")
    ap.add_argument("--http-serve", action="store_true", help="Start a live HTTP endpoint that serves the LAST result")
    ap.add_argument("--http-host", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=4041)
    ap.add_argument("--http-path", default="/hilbert-output")
    ap.add_argument("--buyer-url", default="", help="Optional: http://HOST:PORT/buyer. If set, overrides min-*, budget-per-*, and min-score-per-* from the latest buyer request.")
    ap.add_argument("--loop", action="store_true", help="Re-run discovery forever with a fixed sleep")
    ap.add_argument("--busy-secs", type=float, default=30.0, help="Fixed seconds to keep serving last results before re-running")

    args = ap.parse_args()

    query_node = _node_name_from_nodejson_or_hostname()

    # ---------- Load geometry ONCE ----------
    try:
        nodes = load_geometry(args.geom_url, timeout=8)
    except Exception as e:
        raise SystemExit(f"[geom] failed to load geometry from {args.geom_url}: {e}")
    H = HilbertIndex(nodes, p_bits=NET_P_BITS)
    if query_node not in H.idx:
        raise SystemExit(f"query node {query_node} not found in geometry ({args.geom_url})")
    rtts = extract_rtts(nodes)
    # ----------------------------------------

    # live HTTP server
    state = {"payload": {"scope": "none", "results": []}, "lock": threading.Lock()}
    if args.http_serve:
        start_live_http_server(state, args.http_host, args.http_port, args.http_path)

    def do_cycle():
        if args.buyer_url:
            apply_buyer_overrides(args, ap)
        payload = run_once(query_node, args, H, rtts)
        with state["lock"]:
            state["payload"] = payload
        return payload

    try:
        if args.loop:
            print(f"[loop] fixed sleep {args.busy_secs}s; endpoint (if enabled) serves the latest results between cycles.")
            while True:
                do_cycle()
                time.sleep(max(0.0, float(args.busy_secs)))
        else:
            payload = do_cycle()
            if not args.http_serve:
                if payload.get("results"):
                    df = pd.DataFrame(payload["results"]).set_index("name")
                    print(df.to_string())
                else:
                    print("(no results)")
            else:
                print("[single-shot] results published to HTTP; press Ctrl+C to stop the server.")
                while True:
                    time.sleep(3600)
    except KeyboardInterrupt:
        print("\n[main] stopped by user")

if __name__ == "__main__":
    main()

