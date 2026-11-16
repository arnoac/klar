// k-nucleotide, single-threaded, plain Go (Benchmarks Game algorithm): string keys in a built-in map.
package main

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
	"sort"
)

func count(seq string, k int) map[string]int {
	m := map[string]int{}
	for i := 0; i+k <= len(seq); i++ {
		m[seq[i:i+k]]++
	}
	return m
}

func main() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<16), 1<<20)
	var buf bytes.Buffer
	inside := false
	for in.Scan() {
		line := in.Bytes()
		if inside {
			if line[0] == '>' {
				break
			}
			buf.Write(line)
		} else if bytes.HasPrefix(line, []byte(">THREE")) {
			inside = true
		}
	}
	seq := string(bytes.ToUpper(buf.Bytes()))
	for k := 1; k <= 2; k++ {
		m := count(seq, k)
		keys := make([]string, 0, len(m))
		for key := range m {
			keys = append(keys, key)
		}
		sort.Slice(keys, func(i, j int) bool {
			if m[keys[i]] != m[keys[j]] {
				return m[keys[i]] > m[keys[j]]
			}
			return keys[i] < keys[j]
		})
		for _, key := range keys {
			fmt.Printf("%s %.3f\n", key, 100*float64(m[key])/float64(len(seq)-k+1))
		}
		fmt.Println()
	}
	for _, q := range []string{"GGT", "GGTA", "GGTATT", "GGTATTTTAATT", "GGTATTTTAATTTATAGT"} {
		fmt.Printf("%d\t%s\n", count(seq, len(q))[q], q)
	}
}
