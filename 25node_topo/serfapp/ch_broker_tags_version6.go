package main

import (
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/hashicorp/serf/client"
)

/*
LAN handler listens for:  ch.ask-remote-res
WAN handler serves:       wan.get-res
*/

// ---------- Fraction-tolerant int (truncate toward zero) ----------

type IntTrunc int

func (it *IntTrunc) UnmarshalJSON(b []byte) error {
	// handle null
	s := strings.TrimSpace(string(b))
	if s == "null" || s == "" {
		*it = 0
		return nil
	}
	// if quoted, strip quotes
	if len(s) >= 2 && ((s[0] == '"' && s[len(s)-1] == '"') || (s[0] == '\'' && s[len(s)-1] == '\'')) {
		s = s[1 : len(s)-1]
		s = strings.TrimSpace(s)
	}
	// try strict int first
	if n, err := strconv.Atoi(s); err == nil {
		*it = IntTrunc(n)
		return nil
	}
	// fall back to float then truncate toward zero
	if f, err := strconv.ParseFloat(s, 64); err == nil {
		*it = IntTrunc(int(f))
		return nil
	}
	// anything else -> zero
	*it = 0
	return nil
}

func (it IntTrunc) Int() int { return int(it) }

// ---------------- Request / Record ----------------

type Req struct {
	RequestID   string   `json:"request_id,omitempty"`
	WantedNames []string `json:"wanted_names,omitempty"`

	MinCPU     IntTrunc `json:"min_cpu,omitempty"`     // accepts 12.9 -> 12
	MinRAM     float64  `json:"min_ram,omitempty"`     // RAM stays float
	MinStorage IntTrunc `json:"min_storage,omitempty"` // accepts 1000.7 -> 1000
	MinGPU     IntTrunc `json:"min_gpu,omitempty"`     // accepts 1.9 -> 1

	// Legacy (kept for compatibility; no longer used in filtering)
	MaxPrice float64 `json:"max_price,omitempty"`

	// Per-unit price caps
	BudgetPerCPU     float64 `json:"budget_per_cpu,omitempty"`
	BudgetPerRAM     float64 `json:"budget_per_ram,omitempty"`
	BudgetPerStorage float64 `json:"budget_per_storage,omitempty"`
	BudgetPerGPU     float64 `json:"budget_per_gpu,omitempty"`

	// Per-unit score floors
	MinScorePerCPU     float64 `json:"min_score_per_cpu,omitempty"`
	MinScorePerRAM     float64 `json:"min_score_per_ram,omitempty"`
	MinScorePerStorage float64 `json:"min_score_per_storage,omitempty"`
	MinScorePerGPU     float64 `json:"min_score_per_gpu,omitempty"`

	Limit IntTrunc `json:"limit,omitempty"` // accepts float, truncates
}

type Rec struct {
	Name    string  `json:"name"`
	IP      string  `json:"ip"`
	CPU     int     `json:"cpu"`
	RAM     float64 `json:"ram"` // RAM is float64
	Storage int     `json:"storage"`
	GPU     int     `json:"gpu"`

	// Per-unit price caps (optional tags -> pointers)
	PricePerCPU     *float64 `json:"price_per_cpu,omitempty"`
	PricePerRAM     *float64 `json:"price_per_ram,omitempty"`
	PricePerStorage *float64 `json:"price_per_storage,omitempty"`
	PricePerGPU     *float64 `json:"price_per_gpu,omitempty"`

	// Per-unit score floors (optional tags -> pointers)
	ScorePerCPU     *float64 `json:"score_per_cpu,omitempty"`
	ScorePerRAM     *float64 `json:"score_per_ram,omitempty"`
	ScorePerStorage *float64 `json:"score_per_storage,omitempty"`
	ScorePerGPU     *float64 `json:"score_per_gpu,omitempty"`
}

// --------------- Utilities ----------------

func mustUint64(v interface{}) uint64 {
	switch x := v.(type) {
	case uint64:
		return x
	case int64:
		return uint64(x)
	case int:
		return uint64(x)
	case uint:
		return uint64(x)
	case float64:
		return uint64(x) // truncates toward zero
	case json.Number:
		// try integer first
		if n, err := x.Int64(); err == nil {
			return uint64(n)
		}
		// then try float
		if f, err := strconv.ParseFloat(string(x), 64); err == nil {
			return uint64(f) // truncate
		}
	case string:
		s := strings.TrimSpace(x)
		// try uint first
		if u, err := strconv.ParseUint(s, 10, 64); err == nil {
			return u
		}
		// fall back to float string
		if f, err := strconv.ParseFloat(s, 64); err == nil && f >= 0 {
			return uint64(f) // truncate
		}
	}
	return 0
}

func dialOK(addr string, d time.Duration) bool {
	c, err := net.DialTimeout("tcp", addr, d)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

// autodiscover LAN & WAN RPCs; WAN ring identified by "-wan" names
func discoverLANWAN() (lanRPC, wanRPC string, err error) {
	var cands []string
	if env := strings.TrimSpace(os.Getenv("SERF_RPC_ADDRS")); env != "" {
		for _, s := range strings.Split(env, ",") {
			if s = strings.TrimSpace(s); s != "" {
				cands = append(cands, s)
			}
		}
	} else {
		for p := 7300; p <= 8000; p++ {
			cands = append(cands, fmt.Sprintf("127.0.0.1:%d", p))
		}
	}

	type ring struct{ addr, self string; names []string }
	var rings []ring

	for _, a := range cands {
		if !dialOK(a, 150*time.Millisecond) {
			continue
		}
		c, e := client.NewRPCClient(a)
		if e != nil {
			continue
		}
		stats, _ := c.Stats()
		self := ""
		if ag, ok := stats["agent"]; ok {
			self = ag["name"]
		}
		ms, e := c.MembersFiltered(nil, "alive", "")
		_ = c.Close()
		if e != nil || self == "" || len(ms) == 0 {
			continue
		}
		var names []string
		for _, m := range ms {
			names = append(names, m.Name)
		}
		rings = append(rings, ring{addr: a, self: self, names: names})
	}
	if len(rings) == 0 {
		return "", "", fmt.Errorf("no Serf RPC found on loopback")
	}

	looksWAN := func(self string, names []string) bool {
		s := strings.ToLower(self)
		if strings.Contains(s, "-wan") || strings.HasSuffix(s, "wan") {
			return true
		}
		cnt := 0
		for _, n := range names {
			nl := strings.ToLower(n)
			if strings.Contains(nl, "-wan") || strings.HasSuffix(nl, "wan") {
				cnt++
			}
		}
		return cnt*2 >= len(names)
	}

	var lan, wan *ring
	for i := range rings {
		r := &rings[i]
		if looksWAN(r.self, r.names) {
			if wan == nil || len(r.names) < len(wan.names) {
				wan = r
			}
		} else {
			if lan == nil || len(r.names) > len(lan.names) {
				lan = r
			}
		}
	}
	if lan == nil && len(rings) == 1 {
		lan = &rings[0]
	}
	if wan == nil && len(rings) == 1 {
		wan = &rings[0]
	}
	if lan == nil || wan == nil {
		return "", "", fmt.Errorf("could not classify WAN/LAN from names")
	}
	return lan.addr, wan.addr, nil
}

// intTag now accepts "12.9" -> 12 (truncate)
func intTag(tags map[string]string, key string) (int, bool) {
	v, ok := tags[key]
	if !ok {
		return 0, false
	}
	s := strings.TrimSpace(v)
	if n, err := strconv.Atoi(s); err == nil {
		return n, true
	}
	if f, err := strconv.ParseFloat(s, 64); err == nil {
		return int(f), true // truncate toward zero
	}
	return 0, false
}

func floatTag(tags map[string]string, key string) (float64, bool) {
	v, ok := tags[key]
	if !ok {
		return 0, false
	}
	f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
	if err != nil {
		return 0, false
	}
	return f, true
}

// ---------- Filtering (per-unit like Python) ----------
//
// Missing price_per_* => treated as +Inf when a budget cap > 0 is set.
// Missing score_per_* => treated as 0 when a score floor > 0 is set.
//
func passResources(
	r Rec,
	minCPU int, minRAM float64, minSto int, minGPU int,
	bCPU, bRAM, bSto, bGPU float64,
	scCPU, scRAM, scSto, scGPU float64,
) bool {
	// resource minimums
	if minCPU > 0 && r.CPU < minCPU {
		return false
	}
	if minRAM > 0 && r.RAM < minRAM {
		return false
	}
	if minSto > 0 && r.Storage < minSto {
		return false
	}
	if minGPU > 0 && r.GPU < minGPU {
		return false
	}

	// price caps (per-unit)
	if bCPU > 0 {
		v := math.Inf(1)
		if r.PricePerCPU != nil {
			v = *r.PricePerCPU
		}
		if v > bCPU {
			return false
		}
	}
	if bRAM > 0 {
		v := math.Inf(1)
		if r.PricePerRAM != nil {
			v = *r.PricePerRAM
		}
		if v > bRAM {
			return false
		}
	}
	if bSto > 0 {
		v := math.Inf(1)
		if r.PricePerStorage != nil {
			v = *r.PricePerStorage
		}
		if v > bSto {
			return false
		}
	}
	if bGPU > 0 {
		v := math.Inf(1)
		if r.PricePerGPU != nil {
			v = *r.PricePerGPU
		}
		if v > bGPU {
			return false
		}
	}

	// score floors (per-unit)
	if scCPU > 0 {
		v := 0.0
		if r.ScorePerCPU != nil {
			v = *r.ScorePerCPU
		}
		if v < scCPU {
			return false
		}
	}
	if scRAM > 0 {
		v := 0.0
		if r.ScorePerRAM != nil {
			v = *r.ScorePerRAM
		}
		if v < scRAM {
			return false
		}
	}
	if scSto > 0 {
		v := 0.0
		if r.ScorePerStorage != nil {
			v = *r.ScorePerStorage
		}
		if v < scSto {
			return false
		}
	}
	if scGPU > 0 {
		v := 0.0
		if r.ScorePerGPU != nil {
			v = *r.ScorePerGPU
		}
		if v < scGPU {
			return false
		}
	}

	return true
}

// Read local LAN members, keep ONLY requested names; collect tags.
func lanRecsForWanted(lan *client.RPCClient, wanted map[string]struct{}) ([]Rec, error) {
	ms, err := lan.MembersFiltered(nil, "alive", "")
	if err != nil {
		return nil, err
	}
	out := make([]Rec, 0, len(ms))
	for _, m := range ms {
		// Only local (non-WAN) members
		if strings.Contains(strings.ToLower(m.Name), "-wan") {
			continue
		}
		// Only if requested
		if _, ok := wanted[m.Name]; !ok {
			continue
		}

		cpu, _ := intTag(m.Tags, "cpu")
		ram, _ := floatTag(m.Tags, "ram")
		sto, _ := intTag(m.Tags, "storage")
		gpu, _ := intTag(m.Tags, "gpu")

		var ppc, ppr, ppst, ppg *float64
		if v, ok := floatTag(m.Tags, "price_per_cpu"); ok {
			ppc = &v
		}
		if v, ok := floatTag(m.Tags, "price_per_ram"); ok {
			ppr = &v
		}
		if v, ok := floatTag(m.Tags, "price_per_storage"); ok {
			ppst = &v
		}
		if v, ok := floatTag(m.Tags, "price_per_gpu"); ok {
			ppg = &v
		}

		var scCPU, scRAM, scSTO, scGPU *float64
		if v, ok := floatTag(m.Tags, "score_per_cpu"); ok {
			scCPU = &v
		}
		if v, ok := floatTag(m.Tags, "score_per_ram"); ok {
			scRAM = &v
		}
		if v, ok := floatTag(m.Tags, "score_per_storage"); ok {
			scSTO = &v
		}
		if v, ok := floatTag(m.Tags, "score_per_gpu"); ok {
			scGPU = &v
		}

		ip := m.Tags["ip"]
		if ip == "" {
			ip = m.Addr.String()
		}

		out = append(out, Rec{
			Name:    m.Name,
			IP:      ip,
			CPU:     cpu,
			RAM:     ram,
			Storage: sto,
			GPU:     gpu,

			PricePerCPU:     ppc,
			PricePerRAM:     ppr,
			PricePerStorage: ppst,
			PricePerGPU:     ppg,

			ScorePerCPU:     scCPU,
			ScorePerRAM:     scRAM,
			ScorePerStorage: scSTO,
			ScorePerGPU:     scGPU,
		})
	}
	return out, nil
}

// ---------------- WAN broadcast & aggregation ----------------

func collectWANBroadcast(wan *client.RPCClient, payload []byte, timeout time.Duration) (recs []Rec, ackCount int, froms []string, err error) {
	acks := make(chan string, 64)
	resps := make(chan client.NodeResponse, 1024)

	param := &client.QueryParam{
		Name:        "wan.get-res",
		Payload:     payload,
		Timeout:     timeout,
		FilterNodes: nil, // broadcast to ALL WAN CHs
		AckCh:       acks,
		RespCh:      resps,
	}
	if err = wan.Query(param); err != nil {
		return nil, 0, nil, err
	}

	ackCh := acks
	respCh := resps
	deadline := time.After(timeout + 200*time.Millisecond)

	for ackCh != nil || respCh != nil {
		select {
		case n, ok := <-ackCh:
			if !ok {
				ackCh = nil
				continue
			}
			ackCount++
			froms = append(froms, n)
		case r, ok := <-respCh:
			if !ok {
				respCh = nil
				continue
			}
			froms = append(froms, r.From)
			var arr []Rec
			if e := json.Unmarshal(r.Payload, &arr); e == nil {
				recs = append(recs, arr...)
			}
		case <-deadline:
			ackCh, respCh = nil, nil
		}
	}
	return recs, ackCount, froms, nil
}

// ---------------- Main ----------------

func main() {
	lanRPC, wanRPC, err := discoverLANWAN()
	if err != nil {
		log.Fatalf("discovery failed: %v", err)
	}
	log.Printf("Detected LAN RPC=%s  WAN RPC=%s", lanRPC, wanRPC)

	lan, err := client.NewRPCClient(lanRPC)
	if err != nil {
		log.Fatalf("LAN connect %s: %v", lanRPC, err)
	}
	wan, err := client.NewRPCClient(wanRPC)
	if err != nil {
		log.Fatalf("WAN connect %s: %v", wanRPC, err)
	}
	defer lan.Close()
	defer wan.Close()

	lanStats, _ := lan.Stats()
	wanStats, _ := wan.Stats()
	selfLAN := "lan-unknown"
	selfWAN := "wan-unknown"
	if ag, ok := lanStats["agent"]; ok && ag["name"] != "" {
		selfLAN = ag["name"]
	}
	if ag, ok := wanStats["agent"]; ok && ag["name"] != "" {
		selfWAN = ag["name"]
	}
	log.Printf("[%s|%s] broker (tags+filters) online", selfLAN, selfWAN)

	lanEv := make(chan map[string]interface{}, 256)
	wanEv := make(chan map[string]interface{}, 256)
	if _, err := lan.Stream("*", lanEv); err != nil {
		log.Fatal(err)
	}
	if _, err := wan.Stream("*", wanEv); err != nil {
		log.Fatal(err)
	}

	// ---------- LAN side: receive from members, broadcast to WAN, aggregate ----------
	go func() {
		for raw := range lanEv {
			if t, _ := raw["Event"].(string); t != "query" {
				continue
			}
			if name, _ := raw["Name"].(string); name != "ch.ask-remote-res" {
				continue
			}

			var payload []byte
			switch v := raw["Payload"].(type) {
			case []byte:
				payload = v
			case string:
				payload = []byte(v)
			default:
				payload = nil
			}
			qid := mustUint64(raw["ID"])

			var req Req
			if err := json.Unmarshal(payload, &req); err != nil {
				log.Printf("[WARN] bad request JSON: %v (payload=%s)", err, string(payload))
			}
			wanted := req.WantedNames
			if len(wanted) == 0 {
				log.Printf("[%s LAN] ch.ask-remote-res req_id=%q has empty wanted_names; returning empty set", selfLAN, req.RequestID)
				_ = lan.Respond(qid, []byte(`{"nodes":[]}`))
				continue
			}

			log.Printf(
				"[%s LAN] recv ch.ask-remote-res req_id=%q wanted=%v "+
					"(min cpu=%d ram=%.3f sto=%d gpu=%d | budgets cpu<=%.3f ram<=%.3f "+
					"sto<=%.3f gpu<=%.3f | floors cpu>=%.3f ram>=%.3f sto>=%.3f gpu>=%.3f) — broadcasting to WAN",
				selfLAN, req.RequestID, wanted,
				req.MinCPU.Int(), req.MinRAM, req.MinStorage.Int(), req.MinGPU.Int(),
				req.BudgetPerCPU, req.BudgetPerRAM, req.BudgetPerStorage, req.BudgetPerGPU,
				req.MinScorePerCPU, req.MinScorePerRAM, req.MinScorePerStorage, req.MinScorePerGPU,
			)

			rems, ackN, froms, qerr := collectWANBroadcast(wan, payload, 4*time.Second)
			if qerr != nil {
				log.Printf("[%s WAN] broadcast error: %v", selfWAN, qerr)
				rems = nil
			}

			// Order and de-dupe by 'wanted' order
			order := map[string]int{}
			for i, n := range wanted {
				order[n] = i
			}
			seen := map[string]bool{}
			uniq := make([]Rec, 0, len(rems))
			for _, r := range rems {
				if _, ok := order[r.Name]; !ok {
					continue // ignore names we didn't ask for
				}
				if seen[r.Name] {
					continue
				}
				seen[r.Name] = true
				uniq = append(uniq, r)
			}

			// Defensive filtering (re-check here)
			filtered := make([]Rec, 0, len(uniq))
			for _, r := range uniq {
				if passResources(
					r,
					req.MinCPU.Int(), req.MinRAM, req.MinStorage.Int(), req.MinGPU.Int(),
					req.BudgetPerCPU, req.BudgetPerRAM, req.BudgetPerStorage, req.BudgetPerGPU,
					req.MinScorePerCPU, req.MinScorePerRAM, req.MinScorePerStorage, req.MinScorePerGPU,
				) {
					filtered = append(filtered, r)
				}
			}

			// Stable order by wanted_names
			sort.SliceStable(filtered, func(i, j int) bool {
				return order[filtered[i].Name] < order[filtered[j].Name]
			})

			// Limit if requested
			lim := req.Limit.Int()
			if lim > 0 && len(filtered) > lim {
				filtered = filtered[:lim]
			}

			log.Printf("[%s WAN] req_id=%q acks=%d responders=%v remote_nodes=%d -> return=%d",
				selfWAN, req.RequestID, ackN, froms, len(rems), len(filtered))

			resp := struct {
				RequestID string `json:"request_id,omitempty"`
				Nodes     []Rec  `json:"nodes"`
			}{RequestID: req.RequestID, Nodes: filtered}
			data, _ := json.Marshal(resp)

			if err := lan.Respond(qid, data); err != nil {
				log.Printf("[%s LAN] respond error: %v", selfLAN, err)
			} else {
				log.Printf("[%s LAN] responded req_id=%q nodes=%d", selfLAN, req.RequestID, len(filtered))
			}
		}
	}()

	// ---------- WAN side: serve ONLY requested names that PASS the filters ----------
	go func() {
		for raw := range wanEv {
			if t, _ := raw["Event"].(string); t != "query" {
				continue
			}
			if name, _ := raw["Name"].(string); name != "wan.get-res" {
				continue
			}

			var payload []byte
			switch v := raw["Payload"].(type) {
			case []byte:
				payload = v
			case string:
				payload = []byte(v)
			default:
				payload = nil
			}
			qid := mustUint64(raw["ID"])

			var req Req
			if err := json.Unmarshal(payload, &req); err != nil {
				log.Printf("[WARN] bad request JSON: %v (payload=%s)", err, string(payload))
			}
			wantedSet := make(map[string]struct{}, len(req.WantedNames))
			for _, n := range req.WantedNames {
				wantedSet[n] = struct{}{}
			}

			log.Printf(
				"[%s WAN] recv wan.get-res req_id=%q wanted=%v "+
					"(filters: cpu=%d ram=%.3f sto=%d gpu=%d | budgets cpu<=%.3f ram<=%.3f "+
					"sto<=%.3f gpu<=%.3f | floors cpu>=%.3f ram>=%.3f sto>=%.3f gpu>=%.3f | limit=%d)",
				selfWAN, req.RequestID, req.WantedNames,
				req.MinCPU.Int(), req.MinRAM, req.MinStorage.Int(), req.MinGPU.Int(),
				req.BudgetPerCPU, req.BudgetPerRAM, req.BudgetPerStorage, req.BudgetPerGPU,
				req.MinScorePerCPU, req.MinScorePerRAM, req.MinScorePerStorage, req.MinScorePerGPU,
				req.Limit.Int(),
			)

			localAll, err := lanRecsForWanted(lan, wantedSet)
			if err != nil {
				log.Printf("[%s WAN] lanRecsForWanted error: %v", selfWAN, err)
				_ = wan.Respond(qid, []byte("[]"))
				continue
			}

			// Apply filters here to cut noise over the wire
			filtered := make([]Rec, 0, len(localAll))
			for _, r := range localAll {
				if passResources(
					r,
					req.MinCPU.Int(), req.MinRAM, req.MinStorage.Int(), req.MinGPU.Int(),
					req.BudgetPerCPU, req.BudgetPerRAM, req.BudgetPerStorage, req.BudgetPerGPU,
					req.MinScorePerCPU, req.MinScorePerRAM, req.MinScorePerStorage, req.MinScorePerGPU,
				) {
					filtered = append(filtered, r)
				}
			}

			// Keep client's wanted order where applicable
			order := map[string]int{}
			for i, n := range req.WantedNames {
				order[n] = i
			}
			sort.SliceStable(filtered, func(i, j int) bool {
				return order[filtered[i].Name] < order[filtered[j].Name]
			})

			lim := req.Limit.Int()
			if lim > 0 && len(filtered) > lim {
				filtered = filtered[:lim]
			}

			data, _ := json.Marshal(filtered)
			_ = wan.Respond(qid, data)
			log.Printf("[%s WAN] replied wan.get-res req_id=%q nodes=%d", selfWAN, req.RequestID, len(filtered))
		}
	}()

	log.Printf("CH broker (tags+filters) running — LAN<'ch.ask-remote-res'> <-> WAN<'wan.get-res'>")
	select {}
}
