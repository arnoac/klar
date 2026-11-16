def inside(cr, ci):
    zr = zi = 0.0
    for _ in range(100):
        zr, zi = zr * zr - zi * zi + cr, 2.0 * zr * zi + ci
        if zr * zr + zi * zi > 4.0:
            return False
    return True
n = 1000
print(sum(1 for y in range(n) for x in range(n) if inside(3.0 * x / n - 2.0, 2.0 * y / n - 1.0)))
