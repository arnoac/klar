/* spectral-norm, single-threaded, plain C (Benchmarks Game algorithm). */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
static double A(int i, int j) { return 1.0 / ((i + j) * (i + j + 1) / 2 + i + 1); }
static void mul_av(int n, const double *v, double *out) {
  for (int i = 0; i < n; i++) { double s = 0; for (int j = 0; j < n; j++) s += A(i, j) * v[j]; out[i] = s; }
}
static void mul_atv(int n, const double *v, double *out) {
  for (int i = 0; i < n; i++) { double s = 0; for (int j = 0; j < n; j++) s += A(j, i) * v[j]; out[i] = s; }
}
static void mul_atav(int n, const double *v, double *out, double *tmp) { mul_av(n, v, tmp); mul_atv(n, tmp, out); }
int main(int argc, char **argv) {
  int n = argc > 1 ? atoi(argv[1]) : 100;
  double *u = malloc(n * sizeof(double)), *v = malloc(n * sizeof(double)), *t = malloc(n * sizeof(double));
  for (int i = 0; i < n; i++) u[i] = 1;
  for (int i = 0; i < 10; i++) { mul_atav(n, u, v, t); mul_atav(n, v, u, t); }
  double vbv = 0, vv = 0;
  for (int i = 0; i < n; i++) { vbv += u[i] * v[i]; vv += v[i] * v[i]; }
  printf("%.9f\n", sqrt(vbv / vv));
  return 0;
}
