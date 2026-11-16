package main

import "fmt"

func inside(cr, ci float64) bool {
	zr, zi := 0.0, 0.0
	for i := 0; i < 100; i++ {
		zr, zi = zr*zr-zi*zi+cr, 2.0*zr*zi+ci
		if zr*zr+zi*zi > 4.0 {
			return false
		}
	}
	return true
}
func main() {
	n := 1000
	count := 0
	for y := 0; y < n; y++ {
		for x := 0; x < n; x++ {
			if inside(3.0*float64(x)/float64(n)-2.0, 2.0*float64(y)/float64(n)-1.0) {
				count++
			}
		}
	}
	fmt.Println(count)
}
