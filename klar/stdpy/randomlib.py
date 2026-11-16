import random

_rng = random.Random()


def seed(n):
    _rng.seed(n)


def int_between(lo, hi):
    return _rng.randint(lo, hi)


def unit_float():
    return _rng.random()
