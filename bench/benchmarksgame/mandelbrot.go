// mandelbrot, single-threaded, plain Go (Benchmarks Game algorithm); prints a checksum of the PBM bytes.
package main

import (
	"fmt"
	"os"
	"strconv"
)

func main() {
	w := 200
	if len(os.Args) > 1 {
		w, _ = strconv.Atoi(os.Args[1])
	}
	h, hash, bits, nbits := w, 0, 0, 0
	for y := 0; y < h; y++ {
		ci := 2.0*float64(y)/float64(h) - 1.0
		for x := 0; x < w; x++ {
			cr := 2.0*float64(x)/float64(w) - 1.5
			var zr, zi, tr, ti float64
			for i := 0; i < 50 && tr+ti <= 4.0; i++ {
				zi = 2.0*zr*zi + ci
				zr = tr - ti + cr
				tr = zr * zr
				ti = zi * zi
			}
			bits *= 2
			if tr+ti <= 4.0 {
				bits++
			}
			nbits++
			if nbits == 8 || x == w-1 {
				for ; nbits < 8; nbits++ {
					bits *= 2
				}
				hash = (hash*31 + bits) % 1000000007
				bits, nbits = 0, 0
			}
		}
	}
	fmt.Printf("P4\n%d %d\n%d\n", w, h, hash)
}
