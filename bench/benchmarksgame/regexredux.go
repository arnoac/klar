// regex-redux, single-threaded, plain Go with the standard regexp package (Benchmarks Game algorithm).
package main

import (
	"fmt"
	"io"
	"os"
	"regexp"
)

func main() {
	data, _ := io.ReadAll(os.Stdin)
	seq := regexp.MustCompile(">.*\n|\n").ReplaceAll(data, nil)
	for _, v := range []string{"agggtaaa|tttaccct", "[cgt]gggtaaa|tttaccc[acg]", "a[act]ggtaaa|tttacc[agt]t",
		"ag[act]gtaaa|tttac[agt]ct", "agg[act]taaa|ttta[agt]cct", "aggg[acg]aaa|ttt[cgt]ccct",
		"agggt[cgt]aa|tt[acg]accct", "agggta[cgt]a|t[acg]taccct", "agggtaa[cgt]|[acg]ttaccct"} {
		fmt.Printf("%s %d\n", v, len(regexp.MustCompile(v).FindAllIndex(seq, -1)))
	}
	out := seq
	for _, s := range [][2]string{{"tHa[Nt]", "<4>"}, {"aND|caN|Ha[DS]|WaS", "<3>"}, {"a[NSt]|BY", "<2>"},
		{"<[^>]*>", "|"}, {`\|[^|][^|]*\|`, "-"}} {
		out = regexp.MustCompile(s[0]).ReplaceAllLiteral(out, []byte(s[1]))
	}
	fmt.Printf("\n%d\n%d\n%d\n", len(data), len(seq), len(out))
}
