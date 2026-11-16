// The kernels of kernels.klar written by hand in plain, idiomatic JavaScript (typed arrays where a JavaScript
// developer would use them), for the WebAssembly benchmark. Same algorithms, same results, same output lines as
// bench.klar: kernel, variant, run, milliseconds, result.

function fixed(v, digits) {
  return v.toFixed(digits);
}

// ---------------------------------------------------------------- nbody
function nbody(steps) {
  const PI = 3.141592653589793, SOLAR_MASS = 4 * PI * PI, DAYS = 365.24;
  const x = new Float64Array([0, 4.84143144246472090e+00, 8.34336671824457987e+00, 1.28943695621391310e+01, 1.53796971148509165e+01]);
  const y = new Float64Array([0, -1.16032004402742839e+00, 4.12479856412430479e+00, -1.51111514016986312e+01, -2.59193146099879641e+01]);
  const z = new Float64Array([0, -1.03622044471123109e-01, -4.03523417114321381e-01, -2.23307578892655734e-01, 1.79258772950371181e-01]);
  const vx = new Float64Array([0, 1.66007664274403694e-03, -2.76742510726862411e-03, 2.96460137564761618e-03, 2.68067772490389322e-03].map(v => v * DAYS));
  const vy = new Float64Array([0, 7.69901118419740425e-03, 4.99852801234917238e-03, 2.37847173959480950e-03, 1.62824170038242295e-03].map(v => v * DAYS));
  const vz = new Float64Array([0, -6.90460016972063023e-05, 2.30417297573763929e-05, -2.96589568540237556e-05, -9.51592254519715870e-05].map(v => v * DAYS));
  const m = new Float64Array([1, 9.54791938424326609e-04, 2.85885980666130812e-04, 4.36624404335156298e-05, 5.15138902046611451e-05].map(v => v * SOLAR_MASS));
  const n = m.length;
  let px = 0, py = 0, pz = 0;
  for (let i = 0; i < n; i++) { px += vx[i] * m[i]; py += vy[i] * m[i]; pz += vz[i] * m[i]; }
  vx[0] = -px / SOLAR_MASS; vy[0] = -py / SOLAR_MASS; vz[0] = -pz / SOLAR_MASS;
  const energy = () => {
    let e = 0;
    for (let i = 0; i < n; i++) {
      e += 0.5 * m[i] * (vx[i] * vx[i] + vy[i] * vy[i] + vz[i] * vz[i]);
      for (let j = i + 1; j < n; j++) {
        const dx = x[i] - x[j], dy = y[i] - y[j], dz = z[i] - z[j];
        e -= m[i] * m[j] / Math.sqrt(dx * dx + dy * dy + dz * dz);
      }
    }
    return e;
  };
  const e0 = energy();
  const dt = 0.01;
  for (let s = 0; s < steps; s++) {
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = x[i] - x[j], dy = y[i] - y[j], dz = z[i] - z[j];
        const d2 = dx * dx + dy * dy + dz * dz;
        const mag = dt / (d2 * Math.sqrt(d2));
        const mi = m[i] * mag, mj = m[j] * mag;
        vx[i] -= dx * mj; vy[i] -= dy * mj; vz[i] -= dz * mj;
        vx[j] += dx * mi; vy[j] += dy * mi; vz[j] += dz * mi;
      }
    }
    for (let k = 0; k < n; k++) { x[k] += dt * vx[k]; y[k] += dt * vy[k]; z[k] += dt * vz[k]; }
  }
  return `${fixed(e0, 9)} ${fixed(energy(), 9)}`;
}

// ---------------------------------------------------------------- spectralnorm
function spectralnorm(n) {
  const a = (i, j) => 1 / ((i + j) * (i + j + 1) / 2 + i + 1);
  const mulAv = (v, out) => { for (let i = 0; i < n; i++) { let s = 0; for (let j = 0; j < n; j++) s += a(i, j) * v[j]; out[i] = s; } };
  const mulAtv = (v, out) => { for (let i = 0; i < n; i++) { let s = 0; for (let j = 0; j < n; j++) s += a(j, i) * v[j]; out[i] = s; } };
  const tmp = new Float64Array(n);
  const mulAtAv = (v, out) => { mulAv(v, tmp); mulAtv(tmp, out); };
  const u = new Float64Array(n).fill(1), v = new Float64Array(n);
  for (let k = 0; k < 10; k++) { mulAtAv(u, v); mulAtAv(v, u); }
  let vbv = 0, vv = 0;
  for (let i = 0; i < n; i++) { vbv += u[i] * v[i]; vv += v[i] * v[i]; }
  return fixed(Math.sqrt(vbv / vv), 9);
}

// ---------------------------------------------------------------- fannkuch-redux
function fannkuch(n) {
  const perm1 = new Int32Array(n), perm = new Int32Array(n), count = new Int32Array(n);
  for (let i = 0; i < n; i++) perm1[i] = i;
  let maxFlips = 0, checksum = 0, permCount = 0, r = n;
  for (;;) {
    while (r !== 1) { count[r - 1] = r; r--; }
    perm.set(perm1);
    let flips = 0, k = perm[0];
    while (k !== 0) {
      for (let i = 0, j = k; i < j; i++, j--) { const t = perm[i]; perm[i] = perm[j]; perm[j] = t; }
      flips++;
      k = perm[0];
    }
    if (flips > maxFlips) maxFlips = flips;
    checksum = permCount % 2 === 0 ? checksum + flips : checksum - flips;
    for (;;) {
      if (r === n) return `${checksum} Pfannkuchen(${n}) = ${maxFlips}`;
      const perm0 = perm1[0];
      for (let i = 0; i < r; i++) perm1[i] = perm1[i + 1];
      perm1[r] = perm0;
      count[r]--;
      if (count[r] > 0) break;
      r++;
    }
    permCount++;
  }
}

// ---------------------------------------------------------------- mandelbrot (checksum of the PBM bitmap)
function mandelbrot(size) {
  let hash = 0, bits = 0, nbits = 0;
  for (let y = 0; y < size; y++) {
    const ci = 2 * y / size - 1;
    for (let x = 0; x < size; x++) {
      const cr = 2 * x / size - 1.5;
      let zr = 0, zi = 0, tr = 0, ti = 0, i = 0;
      while (i < 50 && tr + ti <= 4) {
        zi = 2 * zr * zi + ci;
        zr = tr - ti + cr;
        tr = zr * zr;
        ti = zi * zi;
        i++;
      }
      bits = bits * 2 + (tr + ti <= 4 ? 1 : 0);
      nbits++;
      if (nbits === 8 || x === size - 1) {
        while (nbits < 8) { bits *= 2; nbits++; }
        hash = (hash * 31 + bits) % 1000000007;
        bits = 0; nbits = 0;
      }
    }
  }
  return hash;
}

// ---------------------------------------------------------------- binary-trees
class Node { constructor(left, right) { this.left = left; this.right = right; } }
function bottomUp(depth) { return depth > 0 ? new Node(bottomUp(depth - 1), bottomUp(depth - 1)) : new Node(null, null); }
function check(t) { return t === null ? 0 : 1 + check(t.left) + check(t.right); }
function binarytrees(n) {
  const minDepth = 4, maxDepth = Math.max(minDepth + 2, n);
  let total = check(bottomUp(maxDepth + 1));
  const longLived = bottomUp(maxDepth);
  for (let d = minDepth; d <= maxDepth; d += 2) {
    const iterations = 2 ** (maxDepth - d + minDepth);
    for (let i = 0; i < iterations; i++) total += check(bottomUp(d));
  }
  return total + check(longLived);
}

// ---------------------------------------------------------------- smooth
function smooth(xs, passes) {
  const n = xs.length;
  let cur = Float64Array.from(xs);
  for (let p = 0; p < passes; p++) {
    const next = Float64Array.from(cur);
    for (let i = 1; i < n - 1; i++) next[i] = (cur[i - 1] + 2 * cur[i] + cur[i + 1]) / 4;
    cur = next;
  }
  return cur;
}
function checksum(xs) {
  let s = 0;
  for (const x of xs) s += x;
  return `${xs.length} values, sum ${Math.floor(s * 1000 + 0.5)}`;
}

// ---------------------------------------------------------------- driver
export function main(print = console.log) {
  const runs = 5;
  const measure = (kernel, f) => {
    for (let r = 1; r <= runs; r++) {
      const t0 = Date.now();
      const result = f();
      print(`${kernel}\thand-written JS\t${r}\t${Date.now() - t0}\t${result}`);
    }
  };
  const signal = Array.from({ length: 1000000 }, (_, i) => (i % 1000) / 1000);
  measure("nbody 1M", () => nbody(1000000));
  measure("spectralnorm 1000", () => spectralnorm(1000));
  measure("fannkuch 10", () => fannkuch(10));
  measure("mandelbrot 2000", () => String(mandelbrot(2000)));
  measure("binarytrees 16", () => String(binarytrees(16)));
  measure("smooth 1M floats x 10", () => checksum(smooth(signal, 10)));
}
