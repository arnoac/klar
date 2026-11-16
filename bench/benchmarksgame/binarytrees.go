// binary-trees, single-threaded, plain Go with its garbage collector (Benchmarks Game algorithm).
package main

import (
	"fmt"
	"os"
	"strconv"
)

type Node struct{ l, r *Node }

func bottomUp(d int) *Node {
	if d > 0 {
		return &Node{bottomUp(d - 1), bottomUp(d - 1)}
	}
	return &Node{}
}

func (n *Node) check() int {
	if n.l == nil {
		return 1
	}
	return 1 + n.l.check() + n.r.check()
}

func main() {
	n := 10
	if len(os.Args) > 1 {
		n, _ = strconv.Atoi(os.Args[1])
	}
	minD, maxD := 4, n
	if minD+2 > n {
		maxD = minD + 2
	}
	fmt.Printf("stretch tree of depth %d\t check: %d\n", maxD+1, bottomUp(maxD+1).check())
	ll := bottomUp(maxD)
	for d := minD; d <= maxD; d += 2 {
		it, tot := 1<<uint(maxD-d+minD), 0
		for i := 0; i < it; i++ {
			tot += bottomUp(d).check()
		}
		fmt.Printf("%d\t trees of depth %d\t check: %d\n", it, d, tot)
	}
	fmt.Printf("long lived tree of depth %d\t check: %d\n", maxD, ll.check())
}
