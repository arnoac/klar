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

`use textkit { ... }` loads textkit's lib.klar; `use textkit.case { ... }` loads case.klar in its folder.
Git dependencies (and their own dependencies, flattened) live in .klar/deps/, Python packages in .klar/python/.

    klar new NAME                      a new package folder
    klar add NAME --path DIR           depend on a local package
    klar add NAME --git URL [--tag T | --rev R | --branch B]
    klar add --python NAME==VERSION    a pinned Python package for externs
    klar install                       fetch what klar.toml and klar.lock name
    klar update [NAME...]              move git dependencies to the newest commit their tag/branch allows
"""

import os
import re
import shutil
import subprocess
import sys

from .errors import KlarError

MANIFEST, LOCK = "klar.toml", "klar.lock"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
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
    for name in sorted(py):
        out += ["[[python]]", f"name = {_q(name)}", f"version = {_q(py[name])}", ""]
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
    git_out, py_out, py_from = {}, {}, {}
    seen = {}                                             # name -> (source key, declared by)
    top = Manifest(root)
    queue = [(top, n) for n in top.deps]
    for name, ver in top.python.items():
        py_out[name], py_from[name] = ver, top.name
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
    if py_out:
        _pip(root, py_out, log)
    write_lock(root, git_out, py_out)
    return git_out, py_out


def _pip(root, reqs, log):
    target = os.path.join(root, ".klar", "python")
    stamp = os.path.join(target, ".klar-installed")
    want = sorted(f"{n}=={v}" for n, v in reqs.items())
    if os.path.exists(stamp):
        with open(stamp, encoding="utf-8") as f:
            if f.read().split() == want:
                return
    log(f"installing Python packages: {' '.join(want)}")
    if os.path.isdir(target):
        shutil.rmtree(target)
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "--target", target, *want], capture_output=True, text=True)
    if r.returncode != 0:
        raise PkgError("pip install failed:\n" + (r.stderr.strip() or r.stdout.strip())[-2000:])
    with open(stamp, "w", encoding="utf-8") as f:
        f.write("\n".join(want))


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


def cmd_new(name):
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
        "main.klar": 'fn main() uses io {\n  print(greeting("world"))\n}\n\nfn greeting(name: str) -> str {\n'
                     '  return "Hello, {name}!"\n}\n\ntest "greeting" {\n  assert greeting("Klar") == "Hello, Klar!"\n}\n',
        ".gitignore": ".klar/\n",
    }
    for f, text in files.items():
        with open(os.path.join(name, f), "w", encoding="utf-8") as fh:
            fh.write(text)
    print(f"created {name}/ (klar.toml, main.klar). Run: cd {name} && klar run main.klar", file=sys.stderr)
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


def cmd_add(name, path=None, git=None, rev=None, tag=None, branch=None, python=None):
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
    print("added " + (python or name), file=sys.stderr)
    return 0


def cmd_install(update=None):
    try:
        root = _root_or_fail()
        git, py = install(root, update)
    except PkgError as ex:
        return _report(ex)
    print(f"{len(git)} git dependencies, {len(py)} Python packages: up to date", file=sys.stderr)
    return 0


def to_klar_error(ex, line=1, col=1, file=None):
    return KlarError("E601", str(ex), line, col, ex.hint, file)
