seed = 42
counts = {}
for i in range(2000000):
    seed = (seed * 1103515245 + 12345) % 2147483648
    word = f"w{seed % 5000}"
    counts[word] = counts.get(word, 0) + 1
best, best_n = "", 0
for w in counts:
    if counts[w] > best_n:
        best, best_n = w, counts[w]
print(len(counts), best, best_n)
