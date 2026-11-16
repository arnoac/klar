"""regex-redux, single-threaded, plain Python (Benchmarks Game algorithm). Reads fasta output on stdin."""
import re
import sys


def main():
    data = sys.stdin.read()
    seq = re.sub(">.*\n|\n", "", data)
    for v in ["agggtaaa|tttaccct", "[cgt]gggtaaa|tttaccc[acg]", "a[act]ggtaaa|tttacc[agt]t",
              "ag[act]gtaaa|tttac[agt]ct", "agg[act]taaa|ttta[agt]cct", "aggg[acg]aaa|ttt[cgt]ccct",
              "agggt[cgt]aa|tt[acg]accct", "agggta[cgt]a|t[acg]taccct", "agggtaa[cgt]|[acg]ttaccct"]:
        print(v, len(re.findall(v, seq)))
    out = seq
    for pat, rep in [("tHa[Nt]", "<4>"), ("aND|caN|Ha[DS]|WaS", "<3>"), ("a[NSt]|BY", "<2>"),
                     ("<[^>]*>", "|"), (r"\|[^|][^|]*\|", "-")]:
        out = re.sub(pat, rep, out)
    print(f"\n{len(data)}\n{len(seq)}\n{len(out)}")


main()
