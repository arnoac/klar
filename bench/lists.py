xs = []
for i in range(5000000):
    xs.append(i)
evens = [x for x in xs if x % 2 == 0]
squares = [x % 1000 * 3 for x in evens]
acc = 0
for x in squares:
    acc += x
print(acc)
