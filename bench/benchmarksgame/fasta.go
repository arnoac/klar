// fasta, single-threaded, plain Go (Benchmarks Game algorithm).
package main

import (
	"bufio"
	"os"
	"strconv"
)

const alu = "GGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTTGGGAGGCCGAGGCGGGCGGATCACCTGAGGTCAGGAGTTCGAGACCAGCCTGGCCAACATGGTGAAACCCCGTCTCTACTAAAAATACAAAAATTAGCCGGGCGTGGTGGCGCGCGCCTGTAATCCCAGCTACTCGGGAGGCTGAGGCAGGAGAATCGCTTGAACCCGGGAGGCGGAGGTTGCAGTGAGCCGAGATCGCGCCACTGCACTCCAGCCTGGGCGACAGAGCGAGACTCCGTCTCAAAAA"

var seed = 42
var out = bufio.NewWriterSize(os.Stdout, 1<<16)

func rnd() float64 {
	seed = (seed*3877 + 29573) % 139968
	return float64(seed) / 139968.0
}

func repeat(hdr, s string, n int) {
	out.WriteString(hdr + "\n")
	k, pos := len(s), 0
	line := make([]byte, 0, 61)
	for n > 0 {
		m := min(n, 60)
		line = line[:0]
		for i := 0; i < m; i++ {
			line = append(line, s[(pos+i)%k])
		}
		line = append(line, '\n')
		out.Write(line)
		pos = (pos + m) % k
		n -= m
	}
}

func random(hdr, chars string, p []float64, n int) {
	out.WriteString(hdr + "\n")
	cum, acc := make([]float64, len(p)), 0.0
	for i, x := range p {
		acc += x
		cum[i] = acc
	}
	line := make([]byte, 0, 61)
	for n > 0 {
		m := min(n, 60)
		line = line[:0]
		for i := 0; i < m; i++ {
			r, j := rnd(), 0
			for j < len(cum)-1 && cum[j] <= r {
				j++
			}
			line = append(line, chars[j])
		}
		line = append(line, '\n')
		out.Write(line)
		n -= m
	}
}

func main() {
	n := 1000
	if len(os.Args) > 1 {
		n, _ = strconv.Atoi(os.Args[1])
	}
	repeat(">ONE Homo sapiens alu", alu, n*2)
	random(">TWO IUB ambiguity codes", "acgtBDHKMNRSVWY", []float64{0.27, 0.12, 0.12, 0.27, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02}, n*3)
	random(">THREE Homo sapiens frequency", "acgt", []float64{0.3029549426680, 0.1979883004921, 0.1975473066391, 0.3015094502008}, n*5)
	out.Flush()
}
