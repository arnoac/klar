package main

import "fmt"

func main() {
	xs := []int64{}
	for i := int64(0); i < 5000000; i++ {
		xs = append(xs, i)
	}
	evens := []int64{}
	for _, x := range xs {
		if x%2 == 0 {
			evens = append(evens, x)
		}
	}
	squares := make([]int64, len(evens))
	for i, x := range evens {
		squares[i] = x % 1000 * 3
	}
	var acc int64
	for _, x := range squares {
		acc += x
	}
	fmt.Println(acc)
}
