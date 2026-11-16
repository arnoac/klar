"""fasta, single-threaded, plain Python (Benchmarks Game algorithm)."""
import sys

ALU = ("GGCCGGGCGCGGTGGCTCACGCCTGTAATCCCAGCACTTTGGGAGGCCGAGGCGGGCGGATCACCTGAGGTCAGGAGTTCGAGACCAGCCTGGCCAACATGGTGAAACCCCGTCTCTACTAAAAATACAAAAATTAGCCGGGCGTGGTGGCGCGCGCCTGTAATCCCAGCTACTCGGGAGGCTGAGGCAGGAGAATCGCTTGAACCCGGGAGGCGGAGGTTGCAGTGAGCCGAGATCGCGCCACTGCACTCCAGCCTGGGCGACAGAGCGAGACTCCGTCTCAAAAA")
seed = 42


def repeat(out, hdr, s, n):
    out.append(hdr)
    k, pos, dbl = len(s), 0, s + s
    while n > 0:
        m = min(n, 60)
        out.append(dbl[pos:pos + m])
        pos = (pos + m) % k
        n -= m


def random_fasta(out, hdr, chars, probs, n):
    global seed
    out.append(hdr)
    cum, acc = [], 0.0
    for p in probs:
        acc += p
        cum.append(acc)
    last = len(cum) - 1
    while n > 0:
        m = min(n, 60)
        line = []
        for _ in range(m):
            seed = (seed * 3877 + 29573) % 139968
            r = seed / 139968.0
            j = 0
            while j < last and cum[j] <= r:
                j += 1
            line.append(chars[j])
        out.append("".join(line))
        n -= m


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    out = []
    repeat(out, ">ONE Homo sapiens alu", ALU, n * 2)
    random_fasta(out, ">TWO IUB ambiguity codes", "acgtBDHKMNRSVWY",
                 [0.27, 0.12, 0.12, 0.27] + [0.02] * 11, n * 3)
    random_fasta(out, ">THREE Homo sapiens frequency", "acgt",
                 [0.3029549426680, 0.1979883004921, 0.1975473066391, 0.3015094502008], n * 5)
    sys.stdout.write("\n".join(out) + "\n")


main()
