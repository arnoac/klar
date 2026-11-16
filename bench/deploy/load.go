// A small HTTP load generator for the Klar Board benchmark (standard library only).
//
//	go run load.go -base http://127.0.0.1:8080 -kind read -c 32 -d 10s
//
// Kinds: page (GET / , the browser app), read (POST /api/board), write (POST /api/add_card), health (GET /healthz).
// It signs up one user per run, creates a board with 20 cards, then hammers the chosen endpoint from -c workers
// that share the session cookie, and prints requests per second and latency percentiles.
package main

import (
	"bytes"
	"flag"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "load:", err)
		os.Exit(1)
	}
}

func main() {
	base := flag.String("base", "http://127.0.0.1:8080", "server address")
	kind := flag.String("kind", "read", "page, read, write or health")
	conc := flag.Int("c", 32, "concurrent workers")
	dur := flag.Duration("d", 10*time.Second, "how long to run")
	gzip := flag.Bool("gzip", true, "accept gzip, as browsers do")
	flag.Parse()

	tr := &http.Transport{MaxIdleConns: *conc * 2, MaxIdleConnsPerHost: *conc * 2, DisableCompression: true}
	client := &http.Client{Transport: tr, Timeout: 30 * time.Second}
	post := func(path, body, cookie string) (*http.Response, []byte) {
		req, _ := http.NewRequest("POST", *base+path, strings.NewReader(body))
		req.Header.Set("content-type", "application/json")
		if cookie != "" {
			req.Header.Set("cookie", cookie)
		}
		res, err := client.Do(req)
		must(err)
		b, _ := io.ReadAll(res.Body)
		res.Body.Close()
		return res, b
	}

	name := fmt.Sprintf("load%d", rand.Intn(1<<30))
	res, b := post("/api/sign_up", fmt.Sprintf(`{"name":%q,"password":"correct horse battery"}`, name), "")
	if res.StatusCode != 200 {
		must(fmt.Errorf("sign up: %d %s", res.StatusCode, b))
	}
	cookie := strings.SplitN(res.Header.Get("set-cookie"), ";", 2)[0]
	res, b = post("/api/create_board", `"load test"`, cookie)
	var board int
	fmt.Sscanf(string(b[bytes.IndexByte(b, ':')+1:]), "%d", &board)
	cols := []string{"Todo", "Doing", "Done"}
	for i := 0; i < 20; i++ {
		post("/api/add_card", fmt.Sprintf(`{"board_id":%d,"title":"card %d","column":%q}`, board, i, cols[i%3]), cookie)
	}

	request := func() (*http.Request, error) {
		switch *kind {
		case "page":
			return http.NewRequest("GET", *base+"/", nil)
		case "health":
			return http.NewRequest("GET", *base+"/healthz", nil)
		case "read":
			return http.NewRequest("POST", *base+"/api/board", strings.NewReader(fmt.Sprint(board)))
		case "write":
			return http.NewRequest("POST", *base+"/api/add_card",
				strings.NewReader(fmt.Sprintf(`{"board_id":%d,"title":"another card","column":"Doing"}`, board)))
		}
		return nil, fmt.Errorf("unknown kind %q", *kind)
	}

	var mu sync.Mutex
	var lat []time.Duration
	errors, bad, bytesIn := 0, 0, int64(0)
	end := time.Now().Add(*dur)
	var wg sync.WaitGroup
	for w := 0; w < *conc; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			var mine []time.Duration
			e, nb, by := 0, 0, int64(0)
			for time.Now().Before(end) {
				req, err := request()
				must(err)
				req.Header.Set("cookie", cookie)
				if *gzip {
					req.Header.Set("accept-encoding", "gzip")
				}
				t := time.Now()
				res, err := client.Do(req)
				if err != nil {
					e++
					continue
				}
				n, _ := io.Copy(io.Discard, res.Body)
				res.Body.Close()
				mine = append(mine, time.Since(t))
				by += n
				if res.StatusCode != 200 {
					nb++
				}
			}
			mu.Lock()
			lat = append(lat, mine...)
			errors += e
			bad += nb
			bytesIn += by
			mu.Unlock()
		}()
	}
	wg.Wait()
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	pct := func(p float64) float64 {
		if len(lat) == 0 {
			return 0
		}
		return float64(lat[int(float64(len(lat)-1)*p)].Microseconds()) / 1000
	}
	fmt.Printf("%-6s c=%-3d %8.0f req/s  p50 %6.2f ms  p99 %7.2f ms  %6.1f KB/req  errors %d  non-200 %d\n",
		*kind, *conc, float64(len(lat))/dur.Seconds(), pct(0.5), pct(0.99),
		float64(bytesIn)/float64(max(len(lat), 1))/1024, errors, bad)
}
