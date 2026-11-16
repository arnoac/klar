total = 0
for i in range(100000000):
    total += (i * i) % 7
print(total)
