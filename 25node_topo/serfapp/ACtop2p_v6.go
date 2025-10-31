// resource_tags_updater.go
package main

/*
Example:
./ACp2p_v2 \
  -offer-url http://localhost:8080/resource_offer \
  -serf ./serf \
  -rpc-addr 127.0.0.1:7373 \
  -http-serve \
  -http-host 0.0.0.0 \
  -http-port 4042 \
  -http-path /members \
  -members-file ./sellers.json \
  -buyers-file ./buyers.json \
  -interval 50s \
  -health-interval 5s
*/

import (
	"bufio"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"math/rand"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const nodeJSONPath = "/opt/serfapp/node.json"

// =======================
// Types
// =======================

type Offer struct {
	CPU     any `json:"cpu"`
	RAM     any `json:"ram"`
	Storage any `json:"storage"`
	GPU     any `json:"gpu"`
}

type nodeFile struct {
	NodeName  string `json:"node_name"`
	Bind      string `json:"bind"`
	Advertise string `json:"advertise"`
	RPCAddr   string `json:"rpc_addr"`
}

type Applied struct {
	CPU             string
	RAM             string
	Storage         string
	GPU             string
	ScorePerCPU     string
	ScorePerGPU     string
	ScorePerStorage string
	ScorePerRAM     string
	PricePerCPU     string
	PricePerGPU     string
	PricePerRAM     string
	PricePerStorage string
}

type BuyerResource struct {
	DemandPerUnit float64 `json:"demand_per_unit"`
	Score         float64 `json:"score"`
	Budget        float64 `json:"budget"`
}
type Buyer struct {
	IP        string                   `json:"ip"`
	Resources map[string]BuyerResource `json:"resources"`
}

// =======================
// Helpers
// =======================

func loadNodeMeta(path string) (selfName, selfIP, rpcAddr string, err error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", "", "", fmt.Errorf("read node.json: %w", err)
	}
	var nf nodeFile
	if err := json.Unmarshal(b, &nf); err != nil {
		return "", "", "", fmt.Errorf("parse node.json: %w", err)
	}

	selfName = strings.TrimSpace(nf.NodeName)

	if nf.Advertise != "" {
		host, _, e := net.SplitHostPort(nf.Advertise)
		if e == nil && host != "" {
			selfIP = host
		} else {
			selfIP = nf.Advertise
			if i := strings.IndexByte(selfIP, ':'); i >= 0 {
				selfIP = selfIP[:i]
			}
		}
	}
	rpcAddr = strings.TrimSpace(nf.RPCAddr)
	return
}

func ipv4ForInterface(ifName string) (string, error) {
	ifi, err := net.InterfaceByName(ifName)
	if err != nil {
		return "", fmt.Errorf("interface %q: %w", ifName, err)
	}
	addrs, err := ifi.Addrs()
	if err != nil {
		return "", fmt.Errorf("interface %q addrs: %w", ifName, err)
	}
	for _, a := range addrs {
		var ip net.IP
		switch v := a.(type) {
		case *net.IPNet:
			ip = v.IP
		case *net.IPAddr:
			ip = v.IP
		}
		if ip == nil || ip.IsLoopback() {
			continue
		}
		ip = ip.To4()
		if ip == nil {
			continue
		}
		return ip.String(), nil
	}
	return "", fmt.Errorf("no IPv4 found on interface %q", ifName)
}

func toFloat(v any) (float64, error) {
	switch t := v.(type) {
	case nil:
		return 0, nil
	case float64:
		return t, nil
	case float32:
		return float64(t), nil
	case int:
		return float64(t), nil
	case int32:
		return float64(t), nil
	case int64:
		return float64(t), nil
	case uint:
		return float64(t), nil
	case uint32:
		return float64(t), nil
	case uint64:
		return float64(t), nil
	case json.Number:
		return t.Float64()
	case string:
		if t == "" {
			return 0, nil
		}
		f, err := strconv.ParseFloat(t, 64)
		if err != nil {
			return 0, err
		}
		return f, nil
	default:
		return 0, fmt.Errorf("unsupported number type %T", v)
	}
}

func toIntRounded(v any) (int, error) {
	f, err := toFloat(v)
	if err != nil {
		return 0, err
	}
	return int(math.Round(f)), nil
}

func formatCapacities(of Offer) (cpu, ram, storage, gpu string, err error) {
	cpuI, err := toIntRounded(of.CPU)
	if err != nil {
		return "", "", "", "", fmt.Errorf("cpu parse: %w", err)
	}
	gpuI, err := toIntRounded(of.GPU)
	if err != nil {
		return "", "", "", "", fmt.Errorf("gpu parse: %w", err)
	}
	stI, err := toIntRounded(of.Storage)
	if err != nil {
		return "", "", "", "", fmt.Errorf("storage parse: %w", err)
	}
	ramF, err := toFloat(of.RAM)
	if err != nil {
		return "", "", "", "", fmt.Errorf("ram parse: %w", err)
	}
	ramRounded := math.Round(ramF*100) / 100
	ramStr := strconv.FormatFloat(ramRounded, 'f', -1, 64)

	return strconv.Itoa(cpuI), ramStr, strconv.Itoa(stI), strconv.Itoa(gpuI), nil
}

func writeMembersFileAtomically(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// backoff helper
func waitBackoff(ctx context.Context, backoff *time.Duration) bool {
	select {
	case <-time.After(*backoff):
	case <-ctx.Done():
		return false
	}
	if *backoff < 10*time.Second {
		*backoff *= 2
	}
	return true
}

// =======================
// HTTP Offer & Prices
// =======================

func fetchOffer(client *http.Client, url string, timeout time.Duration) (Offer, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return Offer{}, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return Offer{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return Offer{}, fmt.Errorf("non-2xx status: %d", resp.StatusCode)
	}

	dec := json.NewDecoder(resp.Body)
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil {
		return Offer{}, fmt.Errorf("decode error: %w", err)
	}

	valOrZero := func(key string) any {
		if v, ok := m[key]; ok && v != nil {
			if s, isStr := v.(string); isStr && s == "" {
				return 0
			}
			return v
		}
		return 0
	}

	return Offer{
		CPU:     valOrZero("cpu"),
		RAM:     valOrZero("ram"),
		Storage: valOrZero("storage"),
		GPU:     valOrZero("gpu"),
	}, nil
}

func fetchPriceFromURL(client *http.Client, url string, timeout time.Duration) (priceKey string, priceVal string, err error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", "", err
	}
	resp, err := client.Do(req)
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", "", fmt.Errorf("non-2xx status: %d", resp.StatusCode)
	}

	var arr []map[string]any
	dec := json.NewDecoder(resp.Body)
	dec.UseNumber()
	if err := dec.Decode(&arr); err != nil {
		return "", "", fmt.Errorf("decode error: %w", err)
	}
	if len(arr) == 0 {
		return "", "", fmt.Errorf("empty array")
	}

	elem := arr[0]

	rawPrice, ok := elem["price"]
	if !ok {
		return "", "", fmt.Errorf("missing 'price'")
	}
	priceF, err := toFloat(rawPrice)
	if err != nil {
		return "", "", fmt.Errorf("price parse: %w", err)
	}
	price := strconv.FormatFloat(math.Round(priceF*100)/100, 'f', 2, 64)

	var rType string
	for k, v := range elem {
		if strings.EqualFold(k, "resource type") {
			if s, ok := v.(string); ok {
				rType = s
			}
			break
		}
	}
	if rType == "" {
		return "", "", fmt.Errorf("missing 'resource type'")
	}

	key := normalizeResourceType(rType)
	if key == "" {
		return "", "", fmt.Errorf("unknown resource type: %q", rType)
	}
	return key, price, nil
}

func normalizeResourceType(s string) string {
	x := strings.ToLower(strings.TrimSpace(s))
	switch x {
	case "vcpu", "cpu":
		return "cpu"
	case "vgpu", "gpu":
		return "gpu"
	case "ram", "memory":
		return "ram"
	case "storage", "disk":
		return "storage"
	default:
		return ""
	}
}

// repeatable flags
type urlSlice []string

func (u *urlSlice) String() string { return fmt.Sprint([]string(*u)) }
func (u *urlSlice) Set(s string) error {
	*u = append(*u, s)
	return nil
}

// =======================
// Serf helpers
// =======================

func runSerfMembersJSON(ctx context.Context, serfPath, rpcAddr string) ([]byte, error) {
	args := []string{"members", "-format=json"}
	if rpcAddr != "" {
		args = append(args, "-rpc-addr="+rpcAddr)
	}
	cmd := exec.CommandContext(ctx, serfPath, args...)
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return nil, fmt.Errorf("serf members failed: %v | %s", err, string(ee.Stderr))
		}
		return nil, fmt.Errorf("serf members failed: %v", err)
	}
	return out, nil
}

func prettyMembersJSON(raw []byte, selfName, selfIP string) ([]byte, error) {
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		return nil, err
	}
	if selfName != "" {
		payload["self_name"] = selfName
	}
	if selfIP != "" {
		payload["self_ip"] = selfIP
	}
	return json.MarshalIndent(payload, "", "  ")
}

func runSerfTags(ctx context.Context, serfPath, rpcAddr string, sets map[string]string) error {
	if len(sets) == 0 {
		return nil
	}
	args := []string{"tags"}
	if rpcAddr != "" {
		args = append(args, "-rpc-addr="+rpcAddr)
	}
	for k, v := range sets {
		args = append(args, "-set", fmt.Sprintf("%s=%s", k, v))
	}
	cmd := exec.CommandContext(ctx, serfPath, args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("serf tags failed: %v | output: %s", err, string(out))
	}
	return nil
}

// HTTP exposure (optional)
func startHTTPMembersServer(serfPath, rpcAddr, selfName, selfIP, host string, port int, path string) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc(path, func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()

		raw, err := runSerfMembersJSON(ctx, serfPath, rpcAddr)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		pretty, err := prettyMembersJSON(raw, selfName, selfIP)
		if err != nil {
			http.Error(w, "parse error: "+err.Error(), http.StatusBadGateway)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(pretty)
	})

	srv := &http.Server{
		Addr:              fmt.Sprintf("%s:%d", host, port),
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		log.Printf("[http] serving Serf members at http://%s:%d%s", host, port, path)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Printf("[http] server error: %v", err)
		}
	}()
	return srv
}

// =======================
// BUYERS: Event Listener (monitor-only)
// =======================

var buyersMu sync.Mutex

// Upsert (update or insert) a Buyer by IP.
func upsertBuyerByIP(path string, b Buyer) (string, error) {
	buyersMu.Lock()
	defer buyersMu.Unlock()

	var arr []Buyer
	if data, err := os.ReadFile(path); err == nil && len(data) > 0 {
		_ = json.Unmarshal(data, &arr) // best-effort
	}

	action := "added"
	if b.IP != "" {
		for i := range arr {
			if arr[i].IP == b.IP {
				arr[i] = b
				action = "updated"
				break
			}
		}
	}
	if action == "added" {
		arr = append(arr, b)
	}

	out, err := json.MarshalIndent(arr, "", "  ")
	if err != nil {
		return "", err
	}
	if err := writeMembersFileAtomically(path, out); err != nil {
		return "", err
	}
	return action, nil
}

// Parse entire event block and extract bytes from: Payload: []byte{ ... }
func extractPayloadFromMonitorBlock(block string) ([]byte, error) {
	if !strings.Contains(block, "Payload:") {
		return nil, fmt.Errorf("no payload marker")
	}

	re := regexp.MustCompile(`Payload:\s*\[\]byte\{\s*([^}]*)\}`)
	m := re.FindStringSubmatch(block)
	if len(m) < 2 {
		return nil, fmt.Errorf("no []byte payload found")
	}
	content := m[1]

	// Accept tokens like "0x7b", "0X7B", or decimal "123"
	tokRe := regexp.MustCompile(`0[xX]([0-9a-fA-F]{1,2})|(\d{1,3})`)
	toks := tokRe.FindAllStringSubmatch(content, -1)
	if len(toks) == 0 {
		return nil, fmt.Errorf("no byte tokens matched")
	}

	buf := make([]byte, 0, len(toks))
	for _, t := range toks {
		if t[1] != "" { // hex
			h := t[1]
			if len(h) == 1 {
				h = "0" + h
			}
			v, err := strconv.ParseUint(h, 16, 8)
			if err != nil {
				return nil, fmt.Errorf("bad hex %q: %v", t[0], err)
			}
			buf = append(buf, byte(v))
		} else if t[2] != "" { // decimal
			v, err := strconv.ParseUint(t[2], 10, 8)
			if err != nil {
				return nil, fmt.Errorf("bad dec %q: %v", t[0], err)
			}
			buf = append(buf, byte(v))
		}
	}
	return buf, nil
}

// Start serf monitor, capture buyer.request events, upsert into buyers.json
func startSerfBuyerStream(ctx context.Context, serfPath, rpcAddr, buyersFile string) {
	go func() {
		backoff := time.Second

		for {
			select {
			case <-ctx.Done():
				return
			default:
			}

			args := []string{"monitor"}
			if rpcAddr != "" {
				// equals form; if your build needs colon, fallback below
				args = append(args, "-rpc-addr="+rpcAddr)
			}

			cmd := exec.CommandContext(ctx, serfPath, args...)
			stdout, err := cmd.StdoutPipe()
			if err != nil {
				log.Printf("[buyers] monitor stdout pipe error: %v", err)
				if !waitBackoff(ctx, &backoff) { return }
				continue
			}
			stderr, _ := cmd.StderrPipe()

			if err := cmd.Start(); err != nil {
				// Fallback to colon form
				if rpcAddr != "" {
					args = []string{"monitor", "-rpc-addr:" + rpcAddr}
					cmd = exec.CommandContext(ctx, serfPath, args...)
					stdout, err = cmd.StdoutPipe()
				}
				if err != nil || cmd.Start() != nil {
					log.Printf("[buyers] serf monitor start failed: %v", err)
					if !waitBackoff(ctx, &backoff) { return }
					continue
				}
			}

			log.Printf("[buyers] monitoring with: %s %s", serfPath, strings.Join(cmd.Args[1:], " "))

			// Drain stderr quietly
			go func() {
				if stderr == nil { return }
				sc := bufio.NewScanner(stderr)
				for sc.Scan() {
					// suppressed; uncomment to debug:
					// log.Printf("[serf] %s", sc.Text())
				}
			}()

			sc := bufio.NewScanner(stdout)
			sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)

			startRe := regexp.MustCompile(`Received event:\s+user-event:\s*(\S+)`)

			var (
				inBlock       bool
				currEventName string
				lines         []string
			)

			flush := func() {
				if len(lines) == 0 {
					return
				}
				block := strings.Join(lines, "\n")
				lines = lines[:0]

				// Only handle our events
				if currEventName == "buyer.request" || currEventName == "buyers.request" {
					if !strings.Contains(block, "Payload:") {
						inBlock = false
						currEventName = ""
						return
					}
					raw, err := extractPayloadFromMonitorBlock(block)
					if err != nil {
						inBlock = false
						currEventName = ""
						return
					}

					var one Buyer
					if jerr := json.Unmarshal(raw, &one); jerr != nil {
						inBlock = false
						currEventName = ""
						return
					}

					if act, werr := upsertBuyerByIP(buyersFile, one); werr != nil {
						log.Printf("[buyers] write %s error: %v", buyersFile, werr)
					} else {
						log.Printf("[buyers] %s %s (ip=%s, resources=%d)", act, buyersFile, one.IP, len(one.Resources))
					}
				}

				inBlock = false
				currEventName = ""
			}

			for sc.Scan() {
				line := sc.Text()

				if m := startRe.FindStringSubmatch(line); m != nil {
					// New event starts—flush any previous
					if inBlock {
						flush()
					}
					inBlock = true
					currEventName = m[1]
					lines = append(lines, line)
					continue
				}

				if inBlock {
					lines = append(lines, line)
					// Some builds add a blank separator; flush on blank
					if strings.TrimSpace(line) == "" {
						flush()
					}
				}
			}
			if inBlock {
				flush()
			}

			_ = cmd.Wait()
			if !waitBackoff(ctx, &backoff) {
				return
			}
		}
	}()
}

// =======================
// Main
// =======================

func randScore2dp() string {
	v := 1.0 + rand.Float64()*2.0
	if v > 3.0 {
		v = 3.0
	}
	return strconv.FormatFloat(math.Round(v*100)/100, 'f', 2, 64)
}

func main() {
	var (
		offerURL     = flag.String("offer-url", "http://localhost:8080/resource_offer", "Capacity endpoint (cpu, ram, storage, gpu)")
		priceURLs    urlSlice // if empty, auto-build from eth0:8082..8085
		interval     = flag.Duration("interval", 50*time.Second, "Update interval when healthy")
		healthEvery  = flag.Duration("health-interval", 5*time.Second, "Retry interval on errors")
		httpTimeout  = flag.Duration("http-timeout", 6*time.Second, "HTTP timeout")
		serfPath     = flag.String("serf", "./serf", "Path to serf binary")
		rpcAddrFlag  = flag.String("rpc-addr", "", "Serf RPC address (e.g., 127.0.0.1:7373)")
		logNoChanges = flag.Bool("log-no-changes", false, "Log when no tag changes are needed")

		// HTTP exposure flags
		httpServe = flag.Bool("http-serve", true, "Serve Serf members JSON over HTTP")
		httpHost  = flag.String("http-host", "0.0.0.0", "HTTP bind host")
		httpPort  = flag.Int("http-port", 4042, "HTTP port")
		httpPath  = flag.String("http-path", "/members", "HTTP path for Serf members JSON")

		membersFile = flag.String("members-file", "./members.json", "Path to write members JSON after successful tag updates")

		// BUYERS:
		buyersFile   = flag.String("buyers-file", "./buyers.json", "Path to upsert buyer requests from serf events")
		listenBuyers = flag.Bool("listen-buyers", true, "Listen to serf user events and upsert buyer payloads")
	)
	flag.Var(&priceURLs, "price-url", "Price endpoint (repeatable). Expects array with fields: price, resource type")
	flag.Parse()
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	rand.Seed(time.Now().UnixNano())

	// Load self identity (and default rpc-addr) from node.json
	selfName, selfIP, rpcFromFile, err := loadNodeMeta(nodeJSONPath)
	if err != nil {
		log.Printf("[node.json] warning: %v (self_name/self_ip may be empty)", err)
	}
	rpcAddr := *rpcAddrFlag
	if rpcAddr == "" && rpcFromFile != "" {
		rpcAddr = rpcFromFile
	}

	// If no -price-url provided, auto-build from eth0 IP with ports 8082..8085
	if len(priceURLs) == 0 {
		eth0IP, ipErr := ipv4ForInterface("eth0")
		if ipErr != nil {
			log.Printf("[price-url] could not detect eth0 IP: %v (prices will default to 0.00)", ipErr)
		} else {
			for _, p := range []int{8082, 8083, 8084, 8085} {
				priceURLs = append(priceURLs, fmt.Sprintf("http://%s:%d", eth0IP, p))
			}
			log.Printf("[price-url] auto-generated price URLs from eth0: %v", priceURLs)
		}
	}

	client := &http.Client{Timeout: *httpTimeout}

	// HTTP server (optional)
	var srv *http.Server
	if *httpServe {
		srv = startHTTPMembersServer(*serfPath, rpcAddr, selfName, selfIP, *httpHost, *httpPort, *httpPath)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	// BUYERS: start event listener
	if *listenBuyers {
		startSerfBuyerStream(ctx, *serfPath, rpcAddr, *buyersFile)
	}

	// graceful HTTP shutdown on exit
	defer func() {
		if srv != nil {
			shCtx, shCancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer shCancel()
			_ = srv.Shutdown(shCtx)
		}
	}()

	var last Applied
	var haveLast bool

	for {
		select {
		case <-ctx.Done():
			log.Println("Shutting down.")
			return
		default:
		}

		// ----- capacities -----
		offer, err := fetchOffer(client, *offerURL, *httpTimeout)
		if err != nil {
			log.Printf("[health] %s not ready: %v (retry in %s)", *offerURL, err, *healthEvery)
			select {
			case <-time.After(*healthEvery):
				continue
			case <-ctx.Done():
				return
			}
		}
		cpuVal, ramVal, storageVal, gpuVal, err := formatCapacities(offer)
		if err != nil {
			log.Printf("[parse] capacity parse error: %v (retry in %s)", err, *healthEvery)
			select {
			case <-time.After(*healthEvery):
				continue
			case <-ctx.Done():
				return
			}
		}

		// ----- prices (concurrent) -----
		type pres struct {
			key string
			val string
			err error
			url string
		}
		var wg sync.WaitGroup
		results := make([]pres, len(priceURLs))
		wg.Add(len(priceURLs))
		for i, u := range priceURLs {
			go func(i int, u string) {
				defer wg.Done()
				k, v, e := fetchPriceFromURL(client, u, *httpTimeout)
				results[i] = pres{key: k, val: v, err: e, url: u}
			}(i, u)
		}
		wg.Wait()

		var priceCPU, priceGPU, priceRAM, priceStorage *string
		for _, r := range results {
			if r.err != nil {
				log.Printf("[price] %s error: %v", r.url, r.err)
				continue
			}
			switch r.key {
			case "cpu":
				priceCPU = &r.val
			case "gpu":
				priceGPU = &r.val
			case "ram":
				priceRAM = &r.val
			case "storage":
				priceStorage = &r.val
			}
		}
		zero := "0.00"
		if priceCPU == nil {
			priceCPU = &zero
		}
		if priceGPU == nil {
			priceGPU = &zero
		}
		if priceRAM == nil {
			priceRAM = &zero
		}
		if priceStorage == nil {
			priceStorage = &zero
		}

		// ----- random scores -----
		scoreCPU := randScore2dp()
		scoreGPU := randScore2dp()
		scoreStorage := randScore2dp()
		scoreRAM := randScore2dp()

		// ----- compute changes -----
		changes := map[string]string{}
		if !haveLast || last.CPU != cpuVal {
			changes["cpu"] = cpuVal
		}
		if !haveLast || last.RAM != ramVal {
			changes["ram"] = ramVal
		}
		if !haveLast || last.Storage != storageVal {
			changes["storage"] = storageVal
		}
		if !haveLast || last.GPU != gpuVal {
			changes["gpu"] = gpuVal
		}

		if !haveLast || last.PricePerCPU != *priceCPU {
			changes["price_per_cpu"] = *priceCPU
		}
		if !haveLast || last.PricePerGPU != *priceGPU {
			changes["price_per_gpu"] = *priceGPU
		}
		if !haveLast || last.PricePerRAM != *priceRAM {
			changes["price_per_ram"] = *priceRAM
		}
		if !haveLast || last.PricePerStorage != *priceStorage {
			changes["price_per_storage"] = *priceStorage
		}

		if !haveLast || last.ScorePerCPU != scoreCPU {
			changes["score_per_cpu"] = scoreCPU
		}
		if !haveLast || last.ScorePerGPU != scoreGPU {
			changes["score_per_gpu"] = scoreGPU
		}
		if !haveLast || last.ScorePerStorage != scoreStorage {
			changes["score_per_storage"] = scoreStorage
		}
		if !haveLast || last.ScorePerRAM != scoreRAM {
			changes["score_per_ram"] = scoreRAM
		}

		// ----- apply -----
		if len(changes) == 0 {
			if *logNoChanges {
				log.Printf("[update] no changes (cap cpu=%s ram=%s storage=%s gpu=%s | price cpu=%s gpu=%s ram=%s storage=%s)",
					last.CPU, last.RAM, last.Storage, last.GPU,
					last.PricePerCPU, last.PricePerGPU, last.PricePerRAM, last.PricePerStorage,
				)
			}
		} else {
			tagCtx, cancelCmd := context.WithTimeout(ctx, 5*time.Second)
			err = runSerfTags(tagCtx, *serfPath, rpcAddr, changes)
			cancelCmd()
			if err != nil {
				log.Printf("[update] serf tags failed: %v (retry in %s)", err, *healthEvery)
				select {
				case <-time.After(*healthEvery):
					continue
				case <-ctx.Done():
					return
				}
			} else {
				if _, ok := changes["cpu"]; ok {
					last.CPU = cpuVal
				}
				if _, ok := changes["ram"]; ok {
					last.RAM = ramVal
				}
				if _, ok := changes["storage"]; ok {
					last.Storage = storageVal
				}
				if _, ok := changes["gpu"]; ok {
					last.GPU = gpuVal
				}

				if _, ok := changes["price_per_cpu"]; ok {
					last.PricePerCPU = *priceCPU
				}
				if _, ok := changes["price_per_gpu"]; ok {
					last.PricePerGPU = *priceGPU
				}
				if _, ok := changes["price_per_ram"]; ok {
					last.PricePerRAM = *priceRAM
				}
				if _, ok := changes["price_per_storage"]; ok {
					last.PricePerStorage = *priceStorage
				}

				if _, ok := changes["score_per_cpu"]; ok {
					last.ScorePerCPU = scoreCPU
				}
				if _, ok := changes["score_per_gpu"]; ok {
					last.ScorePerGPU = scoreGPU
				}
				if _, ok := changes["score_per_storage"]; ok {
					last.ScorePerStorage = scoreStorage
				}
				if _, ok := changes["score_per_ram"]; ok {
					last.ScorePerRAM = scoreRAM
				}

				haveLast = true
				log.Printf("[update] applied: %v", changes)

				// update members.json
				mctx, mcancel := context.WithTimeout(context.Background(), 5*time.Second)
				raw, merr := runSerfMembersJSON(mctx, *serfPath, rpcAddr)
				mcancel()
				if merr != nil {
					log.Printf("[members file] serf members fetch failed: %v", merr)
				} else {
					pretty, jerr := prettyMembersJSON(raw, selfName, selfIP)
					if jerr != nil {
						log.Printf("[members file] format error: %v", jerr)
					} else if werr := writeMembersFileAtomically(*membersFile, pretty); werr != nil {
						log.Printf("[members file] write error: %v", werr)
					} else {
						log.Printf("[members file] updated %s", *membersFile)
					}
				}
			}
		}

		select {
		case <-time.After(*interval):
		case <-ctx.Done():
			return
		}
	}
}
