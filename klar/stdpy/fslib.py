import os


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def append(path, content):
    with open(path, "a", encoding="utf-8") as f:
        f.write(content)


def exists(path):
    return os.path.exists(path)


def is_dir(path):
    return os.path.isdir(path)


def list_dir(path):
    return sorted(os.listdir(path))


def mkdir(path):
    os.makedirs(path, exist_ok=True)


def remove(path):
    os.remove(path)


def join(parts):
    return os.path.join(*parts) if parts else ""
