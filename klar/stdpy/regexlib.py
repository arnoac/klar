import re


def matches(pattern, s):
    return re.search(pattern, s) is not None


def find_all(pattern, s):
    return [m.group(0) for m in re.finditer(pattern, s)]


def groups(pattern, s):
    m = re.search(pattern, s)
    if m is None:
        return None
    return [g if g is not None else "" for g in m.groups()]


def replace(pattern, s, replacement):
    return re.sub(pattern, replacement, s)


def split(pattern, s):
    return re.split(pattern, s)
