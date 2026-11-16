// reverse-complement, single-threaded, plain Go (Benchmarks Game algorithm). Reads fasta output on stdin.
package main

import (
	"bufio"
	"os"
)

var comp [256]byte

func flush(out *bufio.Writer, header []byte, seq []byte) {
	if header == nil {
		return
	}
	out.Write(header)
	out.WriteByte('\n')
	line := make([]byte, 0, 61)
	for i := len(seq) - 1; i >= 0; i-- {
		line = append(line, comp[seq[i]])
		if len(line) == 60 {
			out.Write(append(line, '\n'))
			line = line[:0]
		}
	}
	if len(line) > 0 {
		out.Write(append(line, '\n'))
	}
}

func main() {
	from, to := "ACBDGHKMNSRUTWVYacbdghkmnsrutwvy", "TGVHCDMKNSYAAWBRTGVHCDMKNSYAAWBR"
	for i := range from {
		comp[from[i]] = to[i]
	}
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<16), 1<<20)
	out := bufio.NewWriterSize(os.Stdout, 1<<16)
	var header, seq []byte
	for in.Scan() {
		line := in.Bytes()
		if len(line) > 0 && line[0] == '>' {
			flush(out, header, seq)
			header = append([]byte(nil), line...)
			seq = seq[:0]
		} else {
			seq = append(seq, line...)
		}
	}
	flush(out, header, seq)
	out.Flush()
}
