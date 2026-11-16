#include <stdio.h>
static int inside(double cr, double ci) {
  double zr = 0, zi = 0;
  for (int i = 0; i < 100; i++) {
    double t = zr * zr - zi * zi + cr;
    zi = 2.0 * zr * zi + ci; zr = t;
    if (zr * zr + zi * zi > 4.0) return 0;
  }
  return 1;
}
int main(void) {
  int n = 1000, count = 0;
  for (int y = 0; y < n; y++) for (int x = 0; x < n; x++)
    if (inside(3.0 * x / n - 2.0, 2.0 * y / n - 1.0)) count++;
  printf("%d\n", count);
  return 0;
}
