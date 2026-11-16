"""Packages: `klar.toml`, dependencies on other Klar packages (a folder path or a git repository), Python
packages for externs, and `klar.lock`, which pins every git dependency to a commit.

    [package]
    name = "myapp"
    version = "0.1.0"

    [dependencies]
    geometry = { path = "../geometry" }
    textkit = { git = "https://github.com/someone/textkit", tag = "v1.2.0" }   # or rev = "...", branch = "..."

    [python]
    requests = "2.32.3"

    [npm]
    marked = "18.2.0"

`use textkit { ... }` loads textkit's lib.klar; `use textkit.case { ... }` loads case.klar in its folder.
Git dependencies (and their own dependencies, flattened) live in .klar/deps/, Python packages in .klar/python/,
npm packages in node_modules/ (klar writes package.json from [npm]; npm's package-lock.json pins the rest).

    klar new NAME                      a new package folder (--web: a browser app)
    klar add ../geometry               depend on a local package (its klar.toml names it)
    klar add https://github.com/someone/textkit@v1.2.0     a git package; @ a tag or a commit
    klar add py:requests==2.32.3       a pinned Python package for externs
    klar add npm:marked[@VERSION]      a pinned npm package for "js:" externs (latest version if none given)
    klar remove NAME                   drop a dependency of any kind
    klar install                       fetch what klar.toml and klar.lock name
    klar update [NAME...]              move git dependencies to the newest commit their tag/branch allows

The long forms `klar add NAME --path DIR`, `--git URL [--tag T | --rev R | --branch B]`, `--python` and `--npm`
still work.
"""

import os
import re
import shutil
import subprocess
import sys

from .errors import KlarError

MANIFEST, LOCK = "klar.toml", "klar.lock"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
EXACT_RE = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
REF_KEYS = ("rev", "tag", "branch")


class PkgError(Exception):
    def __init__(self, msg, hint=None):
        super().__init__(msg)
        self.hint = hint


# ---------------------------------------------------------------------------------------------- manifests

def find_root(start):
    """The folder of the nearest klar.toml at or above `start` (a file or folder), or None."""
    d = os.path.abspath(start)
    if not os.path.isdir(d):
        d = os.path.dirname(d)
    while True:
        if os.path.isfile(os.path.join(d, MANIFEST)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _toml(path):
    import tomllib
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as ex:
        raise PkgError(f"{path}: {ex}", "klar.toml is TOML: `name = \"value\"`, tables like `[dependencies]`")


class Manifest:
    def __init__(self, root):
        self.root = root
        self.path = os.path.join(root, MANIFEST)
        data = _toml(self.path)
        pkg = data.get("package", {})
        self.name = pkg.get("name") or os.path.basename(root)
        self.version = str(pkg.get("version", "0.0.0"))
        self.deps = {}
        for name, spec in data.get("dependencies", {}).items():
            if isinstance(spec, str):
                spec = {"path": spec}
            if not isinstance(spec, dict) or ("path" in spec) == ("git" in spec):
                raise PkgError(f"{self.path}: dependency `{name}` needs exactly one of `path` or `git`",
                               f'{name} = {{ path = "../{name}" }}  or  {name} = {{ git = "https://...", tag = "v1.0.0" }}')
            if sum(k in spec for k in REF_KEYS) > 1:
                raise PkgError(f"{self.path}: dependency `{name}` names more than one of rev, tag, branch")
            if not NAME_RE.match(name):
                raise PkgError(f"{self.path}: dependency name `{name}` must be lower_snake_case (it is used in `use {name}...`)")
            self.deps[name] = spec
        self.python = {}
        for name, ver in data.get("python", {}).items():
            if not isinstance(ver, str) or not ver:
                raise PkgError(f"{self.path}: python package `{name}` needs a version string", f'{name} = "1.2.3"')
            self.python[name] = ver
        self.npm = {}
        for name, ver in data.get("npm", {}).items():
            if not isinstance(ver, str) or not EXACT_RE.match(ver):
                raise PkgError(f"{self.path}: npm package `{name}` must be pinned to one exact version, not {ver!r}",
                               f'{name} = "1.2.3" (ranges like ^1.2 would let a new, unreviewed release in)')
            self.npm[name] = ver

    def dep_dir(self, name, project_root):
        spec = self.deps[name]
        if "path" in spec:
            return os.path.abspath(os.path.join(self.root, spec["path"]))
        return os.path.join(project_root, ".klar", "deps", name)


# ---------------------------------------------------------------------------------------------- resolving `use`

class Resolver:
    """Maps `use name.path` to files for one program. The project is the package around the entry file."""

    def __init__(self, entry_file):
        self.project = find_root(entry_file) if entry_file else find_root(os.getcwd())
        self.manifests = {}
        self.dep_roots = {}                               # absolute folder -> package name

    def manifest(self, root):
        if root not in self.manifests:
            self.manifests[root] = Manifest(root)
        return self.manifests[root]

    def resolve(self, parts, importer):
        """The file for a non-std `use`, or None to fall back to a path relative to the importer."""
        if self.project is None:
            return None
        root = find_root(importer) if importer else self.project
        if root is None:
            return None
        man = self.manifest(root)
        if parts[0] not in man.deps:
            return None
        name = parts[0]
        d = man.dep_dir(name, self.project)
        if not os.path.isfile(os.path.join(d, MANIFEST)):
            hint = "run `klar install`" if "git" in man.deps[name] else f"check the path in {man.path}"
            raise PkgError(f"dependency `{name}` is not installed (no klar.toml in {d})", hint)
        if "git" in man.deps[name]:
            self._check_locked(name, d)
        self.dep_roots[os.path.abspath(d)] = name
        rest = parts[1:] or ["lib"]
        return os.path.join(d, *rest) + ".klar"

    def _check_locked(self, name, d):
        """A git dependency must be checked out at the commit klar.lock pins (cheap: reads .git/HEAD)."""
        if getattr(self, "_lock", None) is None:
            self._lock = read_lock(self.project)[0]
        want = self._lock.get(name, {}).get("commit")
        try:
            with open(os.path.join(d, ".git", "HEAD"), encoding="utf-8") as f:
                head = f.read().strip()
        except OSError:
            return
        if want and head != want:
            raise PkgError(f"dependency `{name}` is checked out at {head[:12]} but klar.lock pins {want[:12]}",
                           "run `klar install`")

    def package_of(self, module_file):
        """The dependency name a module belongs to, or None for the project's own modules."""
        if not isinstance(module_file, str):
            return None
        f = os.path.abspath(module_file)
        best = None
        for d, name in self.dep_roots.items():
            if f.startswith(d + os.sep) and (best is None or len(d) > len(best[0])):
                best = (d, name)
        return best[1] if best else None


def python_dir(entry_file):
    root = find_root(entry_file) if entry_file else None
    return os.path.join(root, ".klar", "python") if root else None


# ---------------------------------------------------------------------------------------------- lock file

def read_lock(root):
    p = os.path.join(root, LOCK)
    if not os.path.exists(p):
        return {}, {}
    data = _toml(p)
    git = {e["name"]: e for e in data.get("package", [])}
    py = {e["name"]: e["version"] for e in data.get("python", [])}
    return git, py


def read_lock_python(root):
    """The [[python]] entries of klar.lock: every installed Python package (including the ones pulled in by
    others) with its version and the sha256 hashes its files may have."""
    p = os.path.join(root, LOCK)
    return _toml(p).get("python", []) if os.path.exists(p) else []


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_lock(root, git, py):
    out = ["# Written by `klar install`. Commit it: it pins every git dependency to an exact commit.", ""]
    for name in sorted(git):
        e = git[name]
        out.append("[[package]]")
        for k in ("name", "git", "rev", "tag", "branch", "commit"):
            if e.get(k):
                out.append(f"{k} = {_q(e[k])}")
        out.append("")
    for e in sorted(py, key=lambda e: e["name"]):
        out += ["[[python]]", f"name = {_q(e['name'])}", f"version = {_q(e['version'])}"]
        if e.get("direct"):
            out.append("direct = true")
        if e.get("hashes"):
            out.append("hashes = [\n" + "".join(f"  {_q(h)},\n" for h in e["hashes"]) + "]")
        out.append("")
    with open(os.path.join(root, LOCK), "w", encoding="utf-8") as f:
        f.write("\n".join(out))


# ---------------------------------------------------------------------------------------------- fetching

def _git(args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise PkgError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _fetch_git(name, spec, dest, locked_commit, log):
    url = spec["git"]
    if os.path.isdir(os.path.join(dest, ".git")):
        try:
            same = _git(["remote", "get-url", "origin"], dest) == url
        except PkgError:
            same = False
        if not same:
            shutil.rmtree(dest)
    if not os.path.isdir(dest):
        log(f"fetching {name} from {url}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        _git(["clone", "-q", url, dest])
    have = lambda ref: subprocess.run(["git", "cat-file", "-e", ref + "^{commit}"], cwd=dest,
                                      capture_output=True).returncode == 0
    if locked_commit:
        target = locked_commit
    elif "rev" in spec:
        target = spec["rev"]
    elif "tag" in spec:
        target = "refs/tags/" + spec["tag"]
    elif "branch" in spec:
        target = "refs/remotes/origin/" + spec["branch"]
    else:
        target = "refs/remotes/origin/HEAD"
    if not locked_commit or not have(target):
        _git(["fetch", "-q", "--tags", "origin"], dest)
        if not have(target) and target == "refs/remotes/origin/HEAD":
            target = "FETCH_HEAD"
    if not have(target):
        what = spec.get("rev") or spec.get("tag") or spec.get("branch") or locked_commit
        raise PkgError(f"dependency `{name}`: `{what}` not found in {url}")
    _git(["checkout", "-q", "--detach", target], dest)
    return _git(["rev-parse", "HEAD"], dest)


def install(root, update=None, log=None):
    """Fetch every dependency (transitively) and pinned Python package; rewrite klar.lock.
    `update` is None (keep locked commits), a set of names to move forward, or "all"."""
    log = log or (lambda m: print(m, file=sys.stderr))
    root = os.path.abspath(root)
    locked_git, locked_py = read_lock(root)
    git_out, py_out, py_from, npm_out, npm_from = {}, {}, {}, {}, {}
    seen = {}                                             # name -> (source key, declared by)
    top = Manifest(root)
    queue = [(top, n) for n in top.deps]
    for name, ver in top.python.items():
        py_out[name], py_from[name] = ver, top.name
    for name, ver in top.npm.items():
        npm_out[name], npm_from[name] = ver, top.name
    while queue:
        man, name = queue.pop(0)
        spec = man.deps[name]
        key = ("path", man.dep_dir(name, root)) if "path" in spec else \
            ("git", spec["git"], tuple((k, spec.get(k)) for k in REF_KEYS))
        if name in seen:
            if seen[name][0] != key:
                raise PkgError(f"`{name}` is required from two different places ({seen[name][1]} and {man.name})",
                               "make both klar.toml files name the same path or git URL and ref")
            continue
        seen[name] = (key, man.name)
        dest = man.dep_dir(name, root)
        if "git" in spec:
            lk = locked_git.get(name)
            same_req = lk and lk.get("git") == spec["git"] and all(lk.get(k) == spec.get(k) for k in REF_KEYS)
            keep = same_req and not (update == "all" or (update and name in update))
            commit = _fetch_git(name, spec, dest, lk["commit"] if keep else None, log)
            entry = {"name": name, "git": spec["git"], "commit": commit, **{k: spec[k] for k in REF_KEYS if k in spec}}
            if not (lk and lk.get("commit") == commit):
                log(f"{name} @ {commit[:12]}")
            git_out[name] = entry
        if not os.path.isfile(os.path.join(dest, MANIFEST)):
            raise PkgError(f"dependency `{name}` ({dest}) has no klar.toml, so it is not a Klar package",
                           "a package is a folder with klar.toml; `klar new` makes one")
        sub = Manifest(dest)
        queue += [(sub, n) for n in sub.deps]
        for pname, ver in sub.python.items():
            if pname in py_out and py_out[pname] != ver:
                raise PkgError(f"Python package `{pname}` is pinned to {py_out[pname]} by {py_from[pname]} "
                               f"and to {ver} by {sub.name}", "pin the same version in both klar.toml files")
            py_out[pname], py_from[pname] = ver, sub.name
        for pname, ver in sub.npm.items():
            if pname in npm_out and npm_out[pname] != ver:
                raise PkgError(f"npm package `{pname}` is pinned to {npm_out[pname]} by {npm_from[pname]} "
                               f"and to {ver} by {sub.name}", "pin the same version in both klar.toml files")
            npm_out[pname], npm_from[pname] = ver, sub.name
    py_lock = _pip(root, py_out, log) if py_out else []
    if npm_out:
        _npm(root, npm_out, log)
    write_lock(root, git_out, py_lock)
    return git_out, py_out


def _norm(name):
    """A Python package name as PyPI compares them (PEP 503): lower case, runs of -_. as one -."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _pip_run(args):
    r = subprocess.run([sys.executable, "-m", "pip", *args, "-q", "--disable-pip-version-check"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr.strip() or r.stdout.strip())[-2000:]
        hint = None
        if "no matching distribution" in msg.lower() or "only-binary" in msg.lower():
            hint = ("Klar installs Python packages only as prebuilt wheels, because building one from source runs the "
                    "package's own setup code on your machine; pick a version that publishes wheels")
        elif "hash" in msg.lower():
            hint = ("a downloaded file does not match the hash in klar.lock: the package changed after it was locked. "
                    "If you trust the new file, delete its [[python]] entry from klar.lock and run `klar install`")
        raise PkgError("pip install failed:\n" + msg, hint)


def _pypi_hashes(name, version):
    """sha256 of every file PyPI has for name==version (all platforms), so the lock works on other machines too.
    Empty when PyPI cannot be asked (offline, another index)."""
    if os.environ.get("PIP_NO_INDEX") or os.environ.get("PIP_INDEX_URL"):
        return []
    import json
    import urllib.request
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return []
    return [f"sha256:{u['digests']['sha256']}" for u in data.get("urls", [])
            if u.get("packagetype") == "bdist_wheel" and u.get("digests", {}).get("sha256")]


def _pip(root, reqs, log):
    """Install the pinned Python packages into .klar/python and return their klar.lock entries.

    Every file is checked against a sha256 hash: hashes recorded in klar.lock when they cover what is asked for,
    otherwise hashes of freshly resolved downloads (plus PyPI's published hashes for the same versions). Only
    prebuilt wheels are installed, so no package code runs during installation."""
    import hashlib
    import tempfile
    target = os.path.join(root, ".klar", "python")
    stamp = os.path.join(target, ".klar-installed")
    locked = {_norm(e["name"]): e for e in read_lock_python(root)}
    want = {_norm(n): v for n, v in reqs.items()}
    direct = {n for n, e in locked.items() if e.get("direct")}
    reuse = direct == set(want) and all(locked[n]["version"] == v and locked[n].get("hashes") for n, v in want.items())
    entries = [locked[n] for n in sorted(locked)] if reuse else None
    with tempfile.TemporaryDirectory() as tmp:
        if entries is None:
            # resolve: download wheels (with their dependencies) without installing or building anything
            log(f"resolving Python packages: {' '.join(f'{n}=={v}' for n, v in sorted(reqs.items()))}")
            dl = os.path.join(tmp, "dl")
            _pip_run(["download", "--only-binary=:all:", "--dest", dl, *(f"{n}=={v}" for n, v in sorted(reqs.items()))])
            entries = []
            for f in sorted(os.listdir(dl)):
                if not f.endswith(".whl"):
                    continue
                name, version = f.split("-")[0], f.split("-")[1]
                with open(os.path.join(dl, f), "rb") as fh:
                    digest = "sha256:" + hashlib.sha256(fh.read()).hexdigest()
                hashes = sorted(set([digest] + _pypi_hashes(name, version)))
                entries.append({"name": _norm(name), "version": version, "hashes": hashes, "direct": _norm(name) in want})
            source = ["--no-index", "--find-links", dl]
        else:
            source = []
        lines = [f"{e['name']}=={e['version']} " + " ".join(f"--hash={h}" for h in e["hashes"]) for e in entries]
        stamp_text = "\n".join(lines)
        if os.path.exists(stamp):
            with open(stamp, encoding="utf-8") as f:
                if f.read() == stamp_text:
                    return entries
        reqfile = os.path.join(tmp, "requirements.txt")
        with open(reqfile, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        names = " ".join(e["name"] + "==" + e["version"] for e in entries)
        log(f"installing Python packages: {names}")
        if os.path.isdir(target):
            shutil.rmtree(target)
        _pip_run(["install", "--require-hashes", "--no-deps", "--only-binary=:all:", *source, "--target", target,
                  "-r", reqfile])
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(stamp_text)
    return entries


NPM_NOTE = "Written by `klar install` from the [npm] table of klar.toml: edit klar.toml, not this file."


def _npm(root, reqs, log):
    """Install npm packages into node_modules/ with npm. klar keeps package.json's dependencies equal to the
    [npm] table; npm's package-lock.json pins every package underneath (commit it). Install scripts are not run."""
    import json
    pj = os.path.join(root, "package.json")
    meta = {}
    if os.path.exists(pj):
        try:
            with open(pj, encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError) as ex:
            raise PkgError(f"{pj}: {ex}", "fix or delete package.json; klar install writes it from klar.toml")
    ours = meta.get("description") == NPM_NOTE or not meta
    deps = dict(meta.get("dependencies", {})) if not ours else {}
    deps.update(reqs)
    if ours:
        meta = {"private": True, "description": NPM_NOTE, "dependencies": {}}
    meta["dependencies"] = dict(sorted(deps.items()))
    text = json.dumps(meta, indent=2) + "\n"
    stamp = os.path.join(root, "node_modules", ".klar-installed")
    old = None
    if os.path.exists(pj):
        with open(pj, encoding="utf-8") as f:
            old = f.read()
    if old == text and os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as f:
            if f.read() == text:
                return
    with open(pj, "w", encoding="utf-8") as f:
        f.write(text)
    npm = shutil.which("npm")
    if not npm:
        raise PkgError("npm packages need Node.js and npm, and `npm` is not on PATH", "install Node.js (it includes npm)")
    log(f"installing npm packages: {' '.join(f'{n}@{v}' for n, v in sorted(reqs.items()))}")
    # With a lock whose dependencies match, `npm ci` installs exactly what it pins and checks every tarball's
    # integrity hash; otherwise `npm install` resolves the change and rewrites package-lock.json.
    cmd = "install"
    lock = os.path.join(root, "package-lock.json")
    if os.path.exists(lock):
        try:
            with open(lock, encoding="utf-8") as f:
                locked = json.load(f).get("packages", {}).get("", {}).get("dependencies", {})
            if locked == meta["dependencies"]:
                cmd = "ci"
        except (OSError, ValueError):
            pass
    r = subprocess.run([npm, cmd, "--no-audit", "--no-fund", "--ignore-scripts", "--loglevel=error"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise PkgError("npm install failed:\n" + (r.stderr.strip() or r.stdout.strip())[-2000:],
                       "check the package name and version in klar.toml [npm]")
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(text)


def npm_latest(name):
    npm = shutil.which("npm")
    if not npm:
        raise PkgError("npm packages need Node.js and npm, and `npm` is not on PATH", "install Node.js (it includes npm)")
    r = subprocess.run([npm, "view", name, "version"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise PkgError(f"npm has no package `{name}`", "check the name on https://www.npmjs.com")
    return r.stdout.strip().splitlines()[-1].strip().strip("'")


NPM_NAME_RE = re.compile(r"^(@[a-z0-9][\w.-]*/)?[a-z0-9][\w.-]*$")


def _toml_key(name):
    return name if re.match(r"^[A-Za-z0-9_-]+$", name) else _q(name)


# ---------------------------------------------------------------------------------------------- editing klar.toml

def _set_entry(path, section, name, value):
    """Set `name = value` in [section] of a TOML file, keeping everything else as written."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    header = f"[{section}]"
    line = f"{name} = {value}"
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["", header, line, ""]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
        pat = re.compile(rf"^\s*{re.escape(name)}\s*=")
        hit = next((i for i in range(start + 1, end) if pat.match(lines[i])), None)
        if hit is not None:
            lines[hit] = line
        else:
            last = end
            while last - 1 > start and lines[last - 1].strip() == "":
                last -= 1
            lines.insert(last, line)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


CONSOLE_MAIN = """fn main() uses io {
  print(greeting("world"))
}

fn greeting(name: str) -> str {
  return "Hello, {name}!"
}

test "greeting" {
  assert greeting("Klar") == "Hello, Klar!"
}
"""

WEB_MAIN = """// A browser app: `klar dev` serves it and reloads on every save; `klar build` makes main.html.

use std.ui { Next, ui_run }
use std.html { Html, h1, p, button, text, on_click }
use std.layout { container, stack, row, gap, Sm }

enum Msg {
  Add,
  Reset,
}

fn init() -> Next[int, Msg] {
  return Next(0, [])
}

fn update(count: int, msg: Msg) -> Next[int, Msg] {
  return match msg {
    Add => Next(count + 1, []),
    Reset => Next(0, []),
  }
}

fn view(count: int) -> Html[Msg] {
  return container([], [
    stack([gap(Sm)], [
      h1([], [text("Hello from Klar")]),
      p([], [text("Clicked {count} times")]),
      row([gap(Sm)], [
        button([on_click(Add)], [text("Add one")]),
        button([on_click(Reset)], [text("Reset")]),
      ]),
    ]),
  ])
}

fn main() uses io {
  ui_run(init(), update, view)
}

test "add and reset" {
  assert update(1, Add).model == 2
  assert update(5, Reset).model == 0
}
"""


def cmd_new(name, web=False):
    if not NAME_RE.match(os.path.basename(name.rstrip("/"))):
        print("klar new: the name must be lower_snake_case, e.g. `klar new my_app`", file=sys.stderr)
        return 2
    if os.path.exists(name):
        print(f"klar new: {name} already exists", file=sys.stderr)
        return 1
    os.makedirs(name)
    pkg = os.path.basename(name.rstrip("/"))
    files = {
        MANIFEST: f'[package]\nname = "{pkg}"\nversion = "0.1.0"\n\n[dependencies]\n',
        "main.klar": WEB_MAIN if web else CONSOLE_MAIN,
        ".gitignore": f".klar/\nnode_modules/\n/{pkg}\n/*.html\n/*.mjs\n",
    }
    for f, text in files.items():
        with open(os.path.join(name, f), "w", encoding="utf-8") as fh:
            fh.write(text)
    first = "klar dev        # open http://127.0.0.1:8000, edit main.klar, watch it reload" if web else \
        "klar run        # Hello, world!"
    print(f"created {name}/ (klar.toml, main.klar). Next:\n\n  cd {name}\n  {first}\n  klar test\n  klar build\n",
          file=sys.stderr)
    return 0


GIT_HOST_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}/[^/\s]+/[^/\s]+")


def parse_source(src, name=None):
    """`klar add SOURCE`: what a single source argument means, as keyword arguments for cmd_add.

        npm:marked  npm:marked@18.2.0        an npm package
        py:requests==2.32.3                   a Python package (pip:, python: work too)
        ../geometry  ./libs/x  /abs/dir       a local Klar package
        https://host/owner/repo[@REF]         a git Klar package; REF is a tag or a commit (github.com/... works too)
    """
    for prefix in ("npm:",):
        if src.startswith(prefix):
            return {"npm": src[len(prefix):]}
    for prefix in ("py:", "pip:", "python:", "pypi:"):
        if src.startswith(prefix):
            return {"python": src[len(prefix):]}
    if src.startswith((".", "/", "~")) or os.path.isdir(src):
        path = os.path.expanduser(src)
        if name is None:
            try:
                name = Manifest(os.path.abspath(path)).name if os.path.isfile(os.path.join(path, MANIFEST)) else None
            except PkgError:
                name = None
            name = name or _guess_name(os.path.basename(os.path.abspath(path)))
        return {"name": name, "path": path}
    if "://" in src or src.startswith("git@") or GIT_HOST_RE.match(src):
        url, ref = src, None
        head, _, last = url.rpartition("/")
        if "@" in last:
            last, _, ref = last.partition("@")
            url = head + "/" + last
        if "://" not in url and not url.startswith("git@"):
            url = "https://" + url
        out = {"name": name or _guess_name(last), "git": url}
        if ref:
            out["rev" if re.match(r"^[0-9a-f]{7,40}$", ref) else "tag"] = ref
        return out
    raise PkgError(f"`{src}` does not say where the package comes from",
                   f"say which kind: klar add npm:{src}  (npm)  |  klar add py:{src}==VERSION  (Python)  |  "
                   f"klar add https://github.com/OWNER/{src}  (a Klar package in git)  |  klar add ./{src}  (a folder)")


def _guess_name(base):
    base = base[:-4] if base.endswith(".git") else base
    return re.sub(r"[^a-z0-9_]", "_", base.lower()).strip("_") or base


def _del_entry(path, section, name):
    """Remove `name = ...` from [section]; True if it was there."""
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    header = f"[{section}]"
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        return False
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    pat = re.compile(rf"^\s*(\"{re.escape(name)}\"|{re.escape(name)})\s*=")
    hit = next((i for i in range(start + 1, end) if pat.match(lines[i])), None)
    if hit is None:
        return False
    del lines[hit]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


def cmd_remove(names):
    try:
        root = _root_or_fail()
    except PkgError as ex:
        return _report(ex)
    man_path = os.path.join(root, MANIFEST)
    with open(man_path, encoding="utf-8") as f:
        before = f.read()
    for name in names:
        name = name.split(":", 1)[1] if name.startswith(("npm:", "py:", "pip:", "python:", "pypi:")) else name
        if not any(_del_entry(man_path, sec, name) for sec in ("dependencies", "python", "npm")):
            with open(man_path, "w", encoding="utf-8") as f:
                f.write(before)
            return _report(PkgError(f"klar.toml has no dependency `{name}`", "see the names in klar.toml"))
    try:
        install(root)
    except PkgError as ex:
        with open(man_path, "w", encoding="utf-8") as f:
            f.write(before)
        _report(ex)
        print("klar.toml was left unchanged", file=sys.stderr)
        return 1
    print("removed " + ", ".join(names), file=sys.stderr)
    return 0


def _report(ex):
    print(f"error[E601]: {ex}", file=sys.stderr)
    if getattr(ex, "hint", None):
        print(f"  hint: {ex.hint}", file=sys.stderr)
    return 1


def _root_or_fail():
    root = find_root(os.getcwd())
    if root is None:
        raise PkgError("no klar.toml in this folder or any folder above it", "create one with `klar new NAME`, "
                       "or write klar.toml with a [package] table")
    return root


def cmd_add(name, path=None, git=None, rev=None, tag=None, branch=None, python=None, npm=None):
    try:
        root = _root_or_fail()
        man_path = os.path.join(root, MANIFEST)
    except PkgError as ex:
        return _report(ex)
    with open(man_path, encoding="utf-8") as f:
        before = f.read()
    try:
        if python:
            pname, sep, ver = python.partition("==")
            if not sep or not ver:
                raise PkgError("pin an exact version: `klar add --python requests==2.32.3`")
            _set_entry(man_path, "python", pname, _q(ver))
        elif npm:
            at = npm.rfind("@")
            nname, ver = (npm[:at], npm[at + 1:]) if at > 0 else (npm, "")
            if not NPM_NAME_RE.match(nname):
                raise PkgError(f"`{nname}` is not an npm package name", "for example: klar add --npm marked@18.2.0")
            ver = ver or npm_latest(nname)
            npm = f"{nname}@{ver}"
            _set_entry(man_path, "npm", _toml_key(nname), _q(ver))
        else:
            if not name or not NAME_RE.match(name):
                raise PkgError("the dependency name must be lower_snake_case: it is what `use NAME...` says")
            if (path is None) == (git is None):
                raise PkgError("say where it comes from: `--path DIR` or `--git URL`")
            if path is not None:
                rel = os.path.relpath(os.path.abspath(path), root)
                value = f"{{ path = {_q(rel)} }}"
            else:
                refs = [(k, v) for k, v in (("rev", rev), ("tag", tag), ("branch", branch)) if v]
                value = "{ " + ", ".join([f"git = {_q(git)}"] + [f"{k} = {_q(v)}" for k, v in refs]) + " }"
            _set_entry(man_path, "dependencies", name, value)
        Manifest(root)                                    # validate what we wrote
        install(root)
    except PkgError as ex:
        with open(man_path, "w", encoding="utf-8") as f:   # put klar.toml (and the checkouts) back
            f.write(before)
        try:
            install(root, log=lambda m: None)
        except PkgError:
            pass
        _report(ex)
        print("klar.toml was left unchanged", file=sys.stderr)
        return 1
    print("added " + (python or npm or name), file=sys.stderr)
    if npm:
        _bind_quietly(root, npm.rpartition("@")[0] if npm.rfind("@") > 0 else npm)
    return 0


def _bind_quietly(root, name):
    """After `klar add npm:NAME`: write npm/NAME.klar from the package's TypeScript types when that is possible
    (Node.js, `typescript` installed, the package ships types) and the file does not exist yet. Never fails the add."""
    from . import npm as npmmod
    out = os.path.join(root, "npm", npmmod.module_file_name(name) + ".klar")
    if os.path.exists(out):
        return
    if not shutil.which("node") or not npmmod._typescript(root):
        print(f"  typed declarations: `klar bind {name}` writes them (it needs `klar add npm:typescript` first)",
              file=sys.stderr)
        return
    cwd = os.getcwd()
    try:
        os.chdir(root)
        npmmod.cmd_bind(name)
    finally:
        os.chdir(cwd)


def cmd_install(update=None):
    try:
        root = _root_or_fail()
        git, py = install(root, update)
        npm = Manifest(root).npm
    except PkgError as ex:
        return _report(ex)
    print(f"{len(git)} git dependencies, {len(py)} Python packages, {len(npm)} npm packages: up to date", file=sys.stderr)
    return 0


def to_klar_error(ex, line=1, col=1, file=None):
    return KlarError("E601", str(ex), line, col, ex.hint, file)
