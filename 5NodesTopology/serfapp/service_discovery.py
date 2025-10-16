#!/usr/bin/env python3
"""
multi_stage_hilbert_router_rtt_then_hilbert_http.py

RTT-first, then raw-Hilbert widening. At each step:
  1) Check LOCAL members (from Serf tags). If any pass resources -> STOP and return them.
  2) Else, forward NON-LOCAL names to Cluster Head (CH). If any pass -> STOP and return them.
  3) Else, widen Δ and retry (for Hilbert phase) or move to Hilbert (after RTT).

NO merging local+remote results; preference is strictly local-first on each step.

Example:
  python3 multi_stage_hilbert_router_rtt_then_hilbert_http.py \
    --query-node clab-century-serf2 \
    --geom-url http://172.20.20.3:4040/cluster-status \
    --rtt-threshold-ms 12 \
    --pct-start 0.02 --max-steps 6 \
    --min-cpu 16 --min-ram 32 --min-storage 1000 --min-gpu 1 --max-price 50 \
    --sort score --limit 10 \
    --rpc-addr 127.0.0.1:7373 --timeout-s 8 \
    --http-serve --http-host 0.0.0.0 --http-port 4041 --http-path /hilbert-output

/cluster-status must include per node:
  - name: str
  - coordinate.Vec: list[float]
  - rtts: dict[name->ms]
"""

import argparse, json, math, subprocess
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from hilbertcurve.hilbertcurve import HilbertCurve

DEFAULT_SERF_RPC = "127.0.0.1:7373"
DEFAULT_TIMEOUT_S = 8
NET_P_BITS = 14

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
        if v is None: return 0
        return int(str(v).strip())
    except Exception:
        return 0

def _to_float(v) -> float:
    try:
        if v is None: return float("nan")
        return float(str(v).strip())
    except Exception:
        return float("nan")

# --------- Local resources from Serf members (LAN) ----------
def get_lan_members(rpc_addr: str) -> pd.DataFrame:
    """
    Returns local members (no -wan) with ip + resource tags.
    Columns: name, ip, cpu, ram, storage, gpu, price, score
    """
    cols = ["name","ip","cpu","ram","storage","gpu","price","score"]
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
            "ram": _to_int(tags.get("ram")),
            "storage": _to_int(tags.get("storage")),
            "gpu": _to_int(tags.get("gpu")),
            "price": _to_float(tags.get("price")),
            "score": _to_float(tags.get("score")),
        })
    return pd.DataFrame(rows, columns=cols)

# ---------- CH request (wanted_names) ----------
def _print_names(title: str, names: List[str]):
    if not names:
        print(f"{title}: (none)")
    else:
        print(f"{title} ({len(names)}): " + ", ".join(names))

def ask_cluster_head_for_remote(min_cpu:int, min_ram:int, min_storage:int, min_gpu:int, max_price:float,
                                wanted_names: List[str], rpc_addr:str, timeout_s:int) -> pd.DataFrame:
    """
    Ask CH (via serf query) for resources for wanted_names (non-local).
    Returns columns: name, ip, cpu, ram, storage, gpu, price, score
    """
    cols = ["name","ip","cpu","ram","storage","gpu","price","score"]
    if not wanted_names:
        return pd.DataFrame(columns=cols)

    payload = {
        **({"min_cpu":min_cpu} if min_cpu>0 else {}),
        **({"min_ram":min_ram} if min_ram>0 else {}),
        **({"min_storage":min_storage} if min_storage>0 else {}),
        **({"min_gpu":min_gpu} if min_gpu>0 else {}),
        **({"max_price":max_price} if max_price>0 else {}),
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
    cols = ["name","ip","cpu","ram","storage","gpu","price","score"]
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
                "ram": _to_int(rec.get("ram") or rec.get("RAM")),
                "storage": _to_int(rec.get("storage") or rec.get("Storage")),
                "gpu": _to_int(rec.get("gpu") or rec.get("GPU")),
                "price": _to_float(rec.get("price") or rec.get("Price")),
                "score": _to_float(rec.get("score") or rec.get("Score")),
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
def filter_by_resources(df: pd.DataFrame, min_cpu:int, min_ram:int,
                        min_storage:int, min_gpu:int, max_price:float) -> pd.DataFrame:
    if df.empty: return df
    x = df.copy()
    price_cmp = x["price"].where(~x["price"].isna(), float("inf"))
    mask = (
        ((min_cpu<=0)     | (x["cpu"]     >= min_cpu)) &
        ((min_ram<=0)     | (x["ram"]     >= min_ram)) &
        ((min_storage<=0) | (x["storage"] >= min_storage)) &
        ((min_gpu<=0)     | (x["gpu"]     >= min_gpu)) &
        ((max_price<=0)   | (price_cmp    <= max_price))
    )
    return x.loc[mask].copy()

def sort_candidates(x: pd.DataFrame, key: str) -> pd.DataFrame:
    if x.empty: return x
    if key == "score":   return x.sort_values(by=["score","name"],   ascending=[False,True])
    if key == "cpu":     return x.sort_values(by=["cpu","name"],     ascending=[False,True])
    if key == "ram":     return x.sort_values(by=["ram","name"],     ascending=[False,True])
    if key == "storage": return x.sort_values(by=["storage","name"], ascending=[False,True])
    if key == "gpu":     return x.sort_values(by=["gpu","name"],     ascending=[False,True])
    if key == "price":   return x.sort_values(by=["price","name"],   ascending=[True,True])
    return x

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

# --------------------------- Main -----------------------------
def main():
    ap = argparse.ArgumentParser(description="RTT-first, then raw-Hilbert widening. Local checked first, remote via CH only if locals fail. No merging.")
    ap.add_argument("--query-node", required=True)
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
    ap.add_argument("--min-ram", type=int, default=0)
    ap.add_argument("--min-storage", type=int, default=0)
    ap.add_argument("--min-gpu", type=int, default=0)
    ap.add_argument("--max-price", type=float, default=0.0)
    ap.add_argument("--sort", choices=["score","cpu","ram","storage","gpu","price","none"], default="score")
    ap.add_argument("--limit", type=int, default=0)

    # Debug + HTTP
    ap.add_argument("--dump-hilbert", action="store_true")
    ap.add_argument("--http-serve", action="store_true", help="serve JSON until interrupted")
    ap.add_argument("--http-host", default="0.0.0.0")
    ap.add_argument("--http-port", type=int, default=4041)
    ap.add_argument("--http-path", default="/hilbert-output")

    args = ap.parse_args()

    # Load geometry + RTTs + local LAN members
    nodes = load_geometry(args.geom_url)
    H = HilbertIndex(nodes, p_bits=NET_P_BITS)
    if args.query_node not in H.idx:
        raise SystemExit(f"query node {args.query_node} not found in geometry")
    rtts = extract_rtts(nodes)

    lan_df = get_lan_members(args.rpc_addr)
    lan_names = set(lan_df["name"].tolist())

    if args.dump_hilbert:
        print("\n=== HILBERT TABLE (sorted by h_raw) ===")
        print(H.df.to_string(index=False))

    # -------- Phase A: RTT slice (sequential: local -> remote) --------
    rtt_map = rtts.get(args.query_node, {})
    rtt_names = [n for n, r in rtt_map.items()
                 if r is not None and r <= args.rtt_threshold_ms and n in H.idx and n != args.query_node]

    local_names = [n for n in rtt_names if n in lan_names]
    remote_names = [n for n in rtt_names if n not in lan_names]
    _print_names("• RTT local names", local_names)
    _print_names("• RTT remote names", remote_names)

    # A1) Local first
    if local_names:
        local_view = lan_df[lan_df["name"].isin(local_names)][["name","ip","cpu","ram","storage","gpu","price","score"]].copy()
        local_view["origin"] = "local"
        local_pass = filter_by_resources(local_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu, args.max_price)
        if not local_pass.empty:
            out = sort_candidates(local_pass, args.sort) if args.sort != "none" else local_pass
            if args.limit > 0: out = out.head(args.limit)
            out["rtt_to_query"] = [rtt_map.get(n, float("nan")) for n in out["name"]]
            payload = {
                "query": args.query_node,
                "scope": "rtt-local",
                "rtt_threshold_ms": args.rtt_threshold_ms,
                "results": out.fillna(np.nan).replace({np.nan: None}).to_dict(orient="records")
            }
            print("\n=== RESULTS (RTT local) ===")
            cols = ["ip","origin","cpu","ram","storage","gpu","price","score","rtt_to_query"]
            print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
            if args.http_serve: serve_json_forever(payload, args.http_host, args.http_port, args.http_path)
            return

    # A2) Remote via CH if locals didn't satisfy
    if remote_names:
        remote_view = ask_cluster_head_for_remote(
            args.min_cpu, args.min_ram, args.min_storage, args.min_gpu, args.max_price,
            wanted_names=remote_names, rpc_addr=args.rpc_addr, timeout_s=args.timeout_s
        )
        if not remote_view.empty:
            remote_view["origin"] = "wan"
            remote_pass = filter_by_resources(remote_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu, args.max_price)
            if not remote_pass.empty:
                out = sort_candidates(remote_pass, args.sort) if args.sort != "none" else remote_pass
                if args.limit > 0: out = out.head(args.limit)
                out["rtt_to_query"] = [rtt_map.get(n, float("nan")) for n in out["name"]]
                payload = {
                    "query": args.query_node,
                    "scope": "rtt-remote",
                    "rtt_threshold_ms": args.rtt_threshold_ms,
                    "results": out.fillna(np.nan).replace({np.nan: None}).to_dict(orient="records")
                }
                print("\n=== RESULTS (RTT remote via CH) ===")
                cols = ["ip","origin","cpu","ram","storage","gpu","price","score","rtt_to_query"]
                print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                if args.http_serve: serve_json_forever(payload, args.http_host, args.http_port, args.http_path)
                return

    print("\n[RTT] no matches (or none passed resources). Widening by raw Hilbert Δ as % of span…")

    # -------- Phase B: raw-Hilbert widening (sequential per window) --------
    hmin = int(H.df["h_raw"].min())
    hmax = int(H.df["h_raw"].max())
    span = max(1, hmax - hmin)
    delta0 = max(1, int(args.pct_start * span))
    q = args.query_node

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
            local_view = lan_df[lan_df["name"].isin(local_names)][["name","ip","cpu","ram","storage","gpu","price","score"]].copy()
            local_view["origin"] = "local"
            local_pass = filter_by_resources(local_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu, args.max_price)
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
                cols = ["ip","origin","cpu","ram","storage","gpu","price","score"]
                print(pd.DataFrame(payload["results"]).set_index("name")[cols].to_string())
                if args.http_serve: serve_json_forever(payload, args.http_host, args.http_port, args.http_path)
                return

        # B2) REMOTE via CH for this window (only if locals failed)
        if remote_names:
            remote_view = ask_cluster_head_for_remote(
                args.min_cpu, args.min_ram, args.min_storage, args.min_gpu, args.max_price,
                wanted_names=remote_names, rpc_addr=args.rpc_addr, timeout_s=args.timeout_s
            )
            if not remote_view.empty:
                remote_view["origin"] = "wan"
                remote_pass = filter_by_resources(remote_view, args.min_cpu, args.min_ram, args.min_storage, args.min_gpu, args.max_price)
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
                    cols = ["ip","origin","cpu","ram","storage","gpu","price","score"]
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
