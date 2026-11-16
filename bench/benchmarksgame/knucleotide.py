"""k-nucleotide, single-threaded, plain Python (Benchmarks Game algorithm). Reads fasta output on stdin."""
import sys
from collections import Counter


def main():
    lines, inside = [], False
    for line in sys.stdin:
        if inside:
            if line.startswith(">"):
                break
            lines.append(line.rstrip("\n"))
        elif line.startswith(">THREE"):
            inside = True
    seq = "".join(lines).upper()

    def count(k):
        return Counter(seq[i:i + k] for i in range(len(seq) - k + 1))

    for k in (1, 2):
        c = count(k)
        total = len(seq) - k + 1
        for key in sorted(c, key=lambda x: (-c[x], x)):
            print(f"{key} {100.0 * c[key] / total:.3f}")
        print()
    for kmer in ("GGT", "GGTA", "GGTATT", "GGTATTTTAATT", "GGTATTTTAATTTATAGT"):
        print(f"{count(len(kmer))[kmer]}\t{kmer}")


main()
