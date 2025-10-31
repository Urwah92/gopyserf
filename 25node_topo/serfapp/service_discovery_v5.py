#!/usr/bin/env python3
"""
multi_stage_hilbert_router_rtt_then_hilbert_http.py

RTT-first, then raw-Hilbert widening. At each step:
  1) Check LOCAL members (from Serf tags). If any pass resources -> STOP and return them.
  2) Else, forward NON-LOCAL names to Cluster Head (CH). If any pass -> STOP and return them.
  3) Else, widen Δ and retry (for Hilbert phase) or move to Hilbert (after RTT).

NO merging local+remote results; preference is strictly local-first on each step.

Example (no --query-node needed; it is read from /opt/serfapp/node.json):
    python3 service_discovery_v2.py \
      --geom-url http://172.20.20.7:4040/cluster-status \
      --rtt-threshold-ms 12 \
      --rpc-addr 127.0.0.1:7373 --timeout-s 8 \
      --sort score_per_cpu --limit 10 \
      --http-serve --http-host 0.0.0.0 --http-port 4041 --http-path /hilbert-output \
      --buyer-url http://127.0.0.1:8090/buyer

/cluster-status must include per node:
  - name: str
  - coordinate.Vec: list[float]
  - rtts: dict[name->ms]
"""

import argparse, json, math, os, socket, subprocess
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hilbertcurve.hilbertcurve import HilbertCurve

DEFAULT_SERF_RPC = "127.0.0.1:7373"
DEFAULT_TIMEOUT_S = 8
NET_P_BITS = 14
NODE_JSON_PATH = "/opt/serfapp/node.json"

# ----------------------------- I/O -----------------------------
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
        return int(float(s))   # or: int(round(float(s))) if you prefer rounding
    except Exception:
        return 0

def _to_float(v) -> float:
    try:
        if v is None: return float("nan")
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

# --------- Local resources from Serf members (LAN) ----------
def get_lan_members(rpc_addr: str) -> pd.DataFrame:
    """
    Returns local members (no -wan) with ip + resource tags.
    Columns: name, ip, cpu, ram, storage, gpu,
             price_per_cpu, price_per_ram, price_per_storage, price_per_gpu,
             score_per_cpu, score_per_ram, score_per_storage, score_per_gpu
    """
    cols = [
        "name","ip","cpu","ram","storage","gpu",
        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"
    ]
    try:
        out = subprocess.run(
            ["./serf","members",f"-rpc-addr={rpc_addr}","-format=json"],
            capture_output=True, text=True, check=True
        ).stdout
        data = json.loads(out)
        members = data.get("members") or data.get("Members") or []
    except Exception as e:
        print(f"[serf members] error: {e}")
        return pd.DataFrame(columns=cols)

    rows = []
    for m in members:
        if not isinstance(m, dict): continue
        name = m.get("name") or m.get("Name")
        if not name: continue
        if "-wan" in str(name).lower():
            continue
        tags = m.get("tags") or m.get("Tags") or {}
        if not isinstance(tags, dict): tags = {}
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

# ---------- CH request (wanted_names) ----------
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
    """
    Ask CH (via serf query) for resources for wanted_names (non-local).
    Returns columns incl. per-unit price and score if provided by CH.
    """
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
        # per-unit budgets
        **({"budget_per_cpu":budget_cpu} if budget_cpu>0 else {}),
        **({"budget_per_ram":budget_ram} if budget_ram>0 else {}),
        **({"budget_per_storage":budget_storage} if budget_storage>0 else {}),
        **({"budget_per_gpu":budget_gpu} if budget_gpu>0 else {}),
        # per-unit score floors
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
            if not isinstance(rec, dict): continue
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
                # per-unit fields (snake_case or CamelCase)
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
        if r["name"] in seen: continue
        seen.add(r["name"]); uniq.append(r)
    return pd.DataFrame(uniq, columns=cols)

# -------------------------- Hilbert ---------------------------
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
        self.df = pd.DataFrame({"name": self.names, "h_raw": self.h_raw}) \
                    .sort_values("h_raw", kind="mergesort") \
                    .reset_index(drop=True)
        self.idx = {nm: i for i, nm in self.df["name"].items()}

    def h(self, name: str) -> Optional[int]:
        if name not in self.idx: return None
        return int(self.df.at[self.idx[name], "h_raw"])

    def names_in_raw_window(self, query: str, delta_raw: int) -> List[str]:
        if query not in self.idx: return []
        qh = float(self.h(query))
        d = max(0.0, float(delta_raw))
        lo, hi = qh - d, qh + d
        mask = (self.df["h_raw"].astype(float) >= lo) & (self.df["h_raw"].astype(float) <= hi)
        out = self.df.loc[mask, "name"].tolist()
        return [n for n in out if n != query and "-wan" not in n.lower()]

# ------------------------- Filtering --------------------------
def _nan_to_inf(series: pd.Series) -> pd.Series:
    return series.where(~series.isna(), float("inf"))

def _nan_to_zero(series: pd.Series) -> pd.Series:
    return series.where(~series.isna(), 0.0)

def filter_by_resources(
    df: pd.DataFrame, min_cpu:int, min_ram:float, min_storage:int, min_gpu:int,
    budget_cpu:float=0.0, budget_ram:float=0.0, budget_storage:float=0.0, budget_gpu:float=0.0,
    min_sc_cpu:float=0.0, min_sc_ram:float=0.0, min_sc_storage:float=0.0, min_sc_gpu:float=0.0
) -> pd.DataFrame:
    if df.empty: return df
    x = df.copy()

    # Per-unit price caps: treat NaN as +inf (won't pass a cap unless cap<=0)
    ppc  = _nan_to_inf(x.get("price_per_cpu",      pd.Series(index=x.index, dtype=float)))
    ppr  = _nan_to_inf(x.get("price_per_ram",      pd.Series(index=x.index, dtype=float)))
    ppst = _nan_to_inf(x.get("price_per_storage",  pd.Series(index=x.index, dtype=float)))
    ppg  = _nan_to_inf(x.get("price_per_gpu",      pd.Series(index=x.index, dtype=float)))

    # Per-unit score floors: treat NaN as 0.0 (fails if a positive floor is requested)
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
    # Sorting rules: higher is better for resources and scores; lower is better for prices.
    ascending = True
    if key in {"cpu","ram","storage","gpu","score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"}:
        ascending = False
    return x.sort_values(by=[key,"name"], ascending=[ascending, True])

# ----------------------- HTTP serving -------------------------
def serve_json_forever(payload: dict, host: str, port: int, path: str):
    import http.server, socketserver, math as _m

    def clean(obj):
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        if isinstance(obj, float):
            if _m.isnan(obj) or _m.isinf(obj):
                return None
        return obj

    payload_bytes = json.dumps(clean(payload), separators=(",",":")).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == path:
                self.send_response(200)
                self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store")
                self.end_headers()
                self.wfile.write(payload_bytes)
            elif self.path == "/healthz":
                self.send_response(200)
                self.send_header("Content-Type","text/plain")
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404); self.end_headers()
        def log_message(self, format, *args): return

    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True; allow_reuse_address = True

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"[http] serving JSON at http://{host}:{port}{path} (Ctrl+C to stop)")
    try: httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt: pass
    finally:
        httpd.server_close(); print("[http] server stopped")

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

# --------------------------- Main -----------------------------
def main():
    ap = argparse.ArgumentParser(description="RTT-first, then raw-Hilbert widening. Local checked first, remote via CH only if locals fail. No merging.")
    # --query-node removed: we auto-read from /opt/serfapp/node.json
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

    # Debug + HTTP
    ap.add_argument("--dump-hilbert", action="store_true")
    ap.add_argument("--http-serve", action="store_true", help="serve JSON until interrupted")
    ap.add_argument("--http-host", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=4041)
    ap.add_argument("--http-path", default="/hilbert-output")
    ap.add_argument("--buyer-url", default="", help="Optional: http://HOST:PORT/buyer. If set, overrides min-*, budget-per-*, and min-score-per-* from buyer request.")

    args = ap.parse_args()

    # Determine our own node name from node.json (or hostname fallback)
    query_node = _node_name_from_nodejson_or_hostname()

    # --- OPTIONAL: auto-pick buyer thresholds from emitter ---
    if args.buyer_url:
        buyer = load_buyer(args.buyer_url, timeout=5)
        res = buyer.get("resources") or {}
        def _get(field, key):
            try:
                v = res.get(field, {}).get(key, None)
                return v
            except Exception:
                return None

        # Demands -> minimums
        min_cpu = int(_get("vcpu", "demand_per_unit") or 0)
        min_ram = float(_get("ram", "demand_per_unit") or 0)
        min_storage = int(_get("storage", "demand_per_unit") or 0)
        min_gpu = int(_get("vgpu", "demand_per_unit") or 0)

        # Per-unit budgets
        b_cpu = float(_get("vcpu", "budget") or 0.0)
        b_ram = float(_get("ram", "budget") or 0.0)
        b_sto = float(_get("storage", "budget") or 0.0)
        b_gpu = float(_get("vgpu", "budget") or 0.0)

        # Per-unit score minimums
        sc_cpu = float(_get("vcpu", "score") or 0.0)
        sc_ram = float(_get("ram", "score") or 0.0)
        sc_sto = float(_get("storage", "score") or 0.0)
        sc_gpu = float(_get("vgpu", "score") or 0.0)

        # Override argparse values (only the "takes from user" part)
        args.min_cpu = max(args.min_cpu, min_cpu)
        args.min_ram = max(args.min_ram, min_ram)
        args.min_storage = max(args.min_storage, min_storage)
        args.min_gpu = max(args.min_gpu, min_gpu)

        if b_cpu > 0: args.budget_per_cpu = b_cpu
        if b_ram > 0: args.budget_per_ram = b_ram
        if b_sto > 0: args.budget_per_storage = b_sto
        if b_gpu > 0: args.budget_per_gpu = b_gpu

        if sc_cpu > 0: args.min_score_per_cpu = sc_cpu
        if sc_ram > 0: args.min_score_per_ram = sc_ram
        if sc_sto > 0: args.min_score_per_storage = sc_sto
        if sc_gpu > 0: args.min_score_per_gpu = sc_gpu

        print(f"[buyer] loaded from {args.buyer_url}: "
              f"min(cpu={args.min_cpu}, ram={args.min_ram}, storage={args.min_storage}, gpu={args.min_gpu}); "
              f"budget(cpu={args.budget_per_cpu}, ram={args.budget_per_ram}, storage={args.budget_per_storage}, gpu={args.budget_per_gpu}); "
              f"score_min(cpu={args.min_score_per_cpu}, ram={args.min_score_per_ram}, storage={args.min_score_per_storage}, gpu={args.min_score_per_gpu})")

    # Load geometry + RTTs + local LAN members
    nodes = load_geometry(args.geom_url)
    H = HilbertIndex(nodes, p_bits=NET_P_BITS)
    if query_node not in H.idx:
        raise SystemExit(f"query node {query_node} not found in geometry ({args.geom_url})")
    rtts = extract_rtts(nodes)

    lan_df = get_lan_members(args.rpc_addr)
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

    # Shorthands
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
        local_pass = filter_by_resources(
            local_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
            b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
        )
        if not local_pass.empty:
            out = sort_candidates(local_pass, args.sort) if args.sort != "none" else local_pass
            if args.limit > 0: out = out.head(args.limit)
            out["rtt_to_query"] = [rtt_map.get(n, float("nan")) for n in out["name"]]
            payload = {
                "query": query_node,
                "scope": "rtt-local",
                "rtt_threshold_ms": args.rtt_threshold_ms,
                "results": out.fillna(np.nan).replace({np.nan: None}).to_dict(orient="records")
            }
            print("\n=== RESULTS (RTT local) ===")
            cols = ["ip","origin","cpu","ram","storage","gpu",
                    "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                    "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu",
                    "rtt_to_query"]
            print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
            if args.http_serve: serve_json_forever(payload, args.http_host, args.http_port, args.http_path)
            return

    # A2) Remote via CH if locals didn't satisfy
    if remote_names:
        remote_view = ask_cluster_head_for_remote(
            args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
            wanted_names=remote_names, rpc_addr=args.rpc_addr, timeout_s=args.timeout_s,
            budget_cpu=b_cpu, budget_ram=b_ram, budget_storage=b_sto, budget_gpu=b_gpu,
            min_sc_cpu=sc_cpu, min_sc_ram=sc_ram, min_sc_storage=sc_sto, min_sc_gpu=sc_gpu
        )
        if not remote_view.empty:
            remote_view["origin"] = "wan"
            remote_pass = filter_by_resources(
                remote_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
            )
            if not remote_pass.empty:
                out = sort_candidates(remote_pass, args.sort) if args.sort != "none" else remote_pass
                if args.limit > 0: out = out.head(args.limit)
                out["rtt_to_query"] = [rtt_map.get(n, float("nan")) for n in out["name"]]
                payload = {
                    "query": query_node,
                    "scope": "rtt-remote",
                    "rtt_threshold_ms": args.rtt_threshold_ms,
                    "results": out.fillna(np.nan).replace({np.nan: None}).to_dict(orient="records")
                }
                print("\n=== RESULTS (RTT remote via CH) ===")
                cols = ["ip","origin","cpu","ram","storage","gpu",
                        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu",
                        "rtt_to_query"]
                print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                if args.http_serve: serve_json_forever(payload, args.http_host, args.http_port, args.http_path)
                return

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

        # Split: local vs remote for THIS window
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
            local_pass = filter_by_resources(
                local_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
            )
            if not local_pass.empty:
                out = sort_candidates(local_pass, args.sort) if args.sort != "none" else local_pass
                if args.limit > 0: out = out.head(args.limit)
                payload = {
                    "query": q,
                    "scope": "hilbert-local",
                    "step": step,
                    "delta_raw": int(delta),
                    "delta_pct_of_span": pct,
                    "results": out.fillna(np.nan).replace({np.nan: None}).to_dict(orient="records"),
                }
                print("\n=== RESULTS (Hilbert window LOCAL) ===")
                cols = ["ip","origin","cpu","ram","storage","gpu",
                        "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                        "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"]
                print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                if args.http_serve: serve_json_forever(payload, args.http_host, args.http_port, args.http_path)
                return

        # B2) REMOTE via CH for this window (only if locals failed)
        if remote_names:
            remote_view = ask_cluster_head_for_remote(
                args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                wanted_names=remote_names, rpc_addr=args.rpc_addr, timeout_s=args.timeout_s,
                budget_cpu=b_cpu, budget_ram=b_ram, budget_storage=b_sto, budget_gpu=b_gpu,
                min_sc_cpu=sc_cpu, min_sc_ram=sc_ram, min_sc_storage=sc_sto, min_sc_gpu=sc_gpu
            )
            if not remote_view.empty:
                remote_view["origin"] = "wan"
                remote_pass = filter_by_resources(
                    remote_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu,
                    b_cpu, b_ram, b_sto, b_gpu, sc_cpu, sc_ram, sc_sto, sc_gpu
                )
                if not remote_pass.empty:
                    out = sort_candidates(remote_pass, args.sort) if args.sort != "none" else remote_pass
                    if args.limit > 0: out = out.head(args.limit)
                    payload = {
                        "query": q,
                        "scope": "hilbert-remote",
                        "step": step,
                        "delta_raw": int(delta),
                        "delta_pct_of_span": pct,
                        "results": out.fillna(np.nan).replace({np.nan: None}).to_dict(orient="records"),
                    }
                    print("\n=== RESULTS (Hilbert window REMOTE via CH) ===")
                    cols = ["ip","origin","cpu","ram","storage","gpu",
                            "price_per_cpu","price_per_ram","price_per_storage","price_per_gpu",
                            "score_per_cpu","score_per_ram","score_per_storage","score_per_gpu"]
                    print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                    if args.http_serve: serve_json_forever(payload, args.http_host, args.http_port, args.http_path)
                    return

        print("• no passing nodes in this window; widening…")

    # If we get here, nothing matched anywhere
    payload = {"query": q, "scope": "none", "results": []}
    print("\n=== RESULTS ===\n(no matches after all steps)")
    if args.http_serve:
        serve_json_forever(payload, args.http_host, args.http_port, args.http_path)

if __name__ == "__main__":
    main()
