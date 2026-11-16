package main

import (
	"fmt"
	"strconv"
)

func main() {
	seed := int64(42)
	counts := map[string]int64{}
	order := []string{}
	for i := 0; i < 2000000; i++ {
		seed = (seed*1103515245 + 12345) % 2147483648
		word := "w" + strconv.FormatInt(seed%5000, 10)
		if _, ok := counts[word]; !ok {
			order = append(order, word)
		}
		counts[word]++
	}
	best, bestN := "", int64(0)
	for _, w := range order {
		if counts[w] > bestN {
			best, bestN = w, counts[w]
		}
	}
	fmt.Println(len(counts), best, bestN)
}
