"""reverse-complement, single-threaded, plain Python (Benchmarks Game algorithm). Reads fasta output on stdin."""
import sys

TABLE = str.maketrans("ACBDGHKMNSRUTWVYacbdghkmnsrutwvy", "TGVHCDMKNSYAAWBRTGVHCDMKNSYAAWBR")


def flush(out, header, lines):
    if header is None:
        return
    out.write(header)
    seq = "".join(lines).translate(TABLE)[::-1]
    for i in range(0, len(seq), 60):
        out.write(seq[i:i + 60] + "\n")


def main():
    out = sys.stdout
    header, lines = None, []
    for line in sys.stdin:
        if line.startswith(">"):
            flush(out, header, lines)
            header, lines = line, []
        else:
            lines.append(line.rstrip("\n"))
    flush(out, header, lines)


main()
