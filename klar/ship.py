"""Getting an app into production: what `klar build` does beyond compiling one file.

    klar build --release            server.klar + app.klar of this project: one executable with the browser app
                                    (and any public/ or static/ folder) built in, gzipped and served with ETags
    klar build --static             a folder of plain files (dist/) for any static host: the browser app, or every
                                    page a std.web site links to from "/", fetched from the running server
    klar build --docker             write a Dockerfile (and .dockerignore) that builds and runs the executable

The executable serves its embedded files first and reads anything else from disk, so it runs from any folder.
DEPLOY.md is the guide.
"""

import gzip
import hashlib
import os
import re
import subprocess
import sys
import tempfile

from .checker import check_source
from .loader import _is_std

ASSET_DIRS = ("public", "static")
SKIP_FILES = re.compile(r"(^|/)\.|\.(klar|db|sqlite|sqlite3|db-wal|db-shm|db-journal)$")


def say(msg, hint=None):
    print(msg, file=sys.stderr)
    if hint:
        print("  hint: " + hint, file=sys.stderr)


# ---------------------------------------------------------------- which file is what

def uses_ui(path):
    """Whether a program is a browser app: it imports std.ui itself (the rule `klar dev` uses)."""
    from .cli import load
    src = load(path)
    prog, errors = check_source(src, path)
    if errors:
        return None
    return any(d.kind == "Use" and not _is_std(d.module) and d.path == ["std", "ui"] for d in prog.decls)


def split_files(files):
    """(backend or None, [browser apps]) for the files of a full-stack build, or None after saying what is wrong."""
    backend, apps = None, []
    for f in files:
        ui = uses_ui(f)
        if ui is None:                     # does not check: build the backend path to report the errors
            return f, []
        if ui:
            apps.append(f)
        elif backend is None:
            backend = f
        else:
            say(f"klar build: two programs that are not browser apps: {backend} and {f}",
                "give one server and any number of std.ui apps: klar build server.klar app.klar")
            return None
    return backend, apps


# ---------------------------------------------------------------- the files built in

def web_page(path):
    """The HTML page for a std.ui app, or the rendered errors."""
    from .cli import WEB_PAGE, check_main_signature, load, report
    from .errors import KlarError
    from .jscompiler import compile_to_js
    src = load(path)
    prog, errors = check_source(src, path)
    if not errors:
        e = check_main_signature(prog, path)
        if e:
            errors = [e]
    if not errors:
        try:
            out = os.path.splitext(path)[0] + ".html"
            js = compile_to_js(prog, path, "web", out)
        except KlarError as e:
            errors = [e]
    if errors:
        report(errors, src, False)
        return None
    stem = os.path.splitext(os.path.basename(path))[0]
    return WEB_PAGE.format(title=stem, head=getattr(js, "head", ""), js=js.replace("</script", "<\\/script"))


def collect_assets(backend, apps):
    """[(path the server asks for, bytes)]: each browser app as NAME.html next to its source (where `klar dev` puts
    it), and the files in public/ and static/ next to the server. None if an app does not build."""
    root = os.path.dirname(os.path.abspath(backend))
    out = []
    for app in apps:
        html = web_page(app)
        if html is None:
            return None
        rel = os.path.relpath(os.path.splitext(os.path.abspath(app))[0] + ".html", root).replace(os.sep, "/")
        out.append((rel, html.encode("utf-8")))
    for d in ASSET_DIRS:
        top = os.path.join(root, d)
        for dirpath, dirs, names in os.walk(top):
            dirs[:] = sorted(x for x in dirs if not x.startswith("."))
            for n in sorted(names):
                p = os.path.join(dirpath, n)
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                if SKIP_FILES.search(rel) or not os.path.isfile(p):
                    continue
                with open(p, "rb") as f:
                    out.append((rel, f.read()))
    return out


def packed(data):
    """(gzipped bytes or None when that saves little, ETag)."""
    z = gzip.compress(data, 9, mtime=0)
    etag = '"' + hashlib.sha256(data).hexdigest()[:20] + '"'
    return (z if len(z) < len(data) * 0.9 else None), etag


_C_BYTE = []
for _b in range(256):
    _c = chr(_b)
    if _c in '"\\?':
        _C_BYTE.append("\\" + _c)
    elif 32 <= _b < 127:
        _C_BYTE.append(_c)
    else:
        _C_BYTE.append("\\%03o" % _b)        # always three digits, so a digit after it is not swallowed


def c_bytes(data):
    """A C string literal for any bytes, in lines of 1,024 source bytes (an escape is never split)."""
    t = _C_BYTE
    return "\n".join('"' + "".join(t[b] for b in data[i:i + 1024]) + '"' for i in range(0, max(len(data), 1), 1024))


def c_assets(assets):
    """C source that fills the runtime's table of built-in files."""
    parts, rows = [], []
    for i, (path, data) in enumerate(assets):
        gz, etag = packed(data)
        parts.append(f"static const char kx_asset_{i}[] = {c_bytes(data)};")
        if gz is not None:
            parts.append(f"static const char kx_asset_{i}z[] = {c_bytes(gz)};")
        rows.append(f"    {{{c_bytes(path.encode())}, kx_asset_{i}, {len(data)}, "
                    + (f"kx_asset_{i}z, {len(gz)}" if gz is not None else "NULL, 0")
                    + f", {c_bytes(etag.encode())}}},")
    parts.append("static const KXAsset kx_asset_table[] = {\n" + "\n".join(rows) + "\n};")
    return "\n/* ---- files built into the executable ---- */\n" + "\n".join(parts) + "\n"


def with_assets(c_src, assets):
    """The generated C program with the table of built-in files, which main() hands to the runtime."""
    at = c_src.rindex("\nint main(int argc, char **argv) {")
    init = "  k_init(argc, argv);\n"
    i = c_src.index(init, at) + len(init)
    return (c_src[:at] + c_assets(assets) + c_src[at:i]
            + f"  kx_assets = kx_asset_table; kx_nassets = {len(assets)};\n" + c_src[i:])


def js_assets(assets):
    """JavaScript that gives the Node runtime the same table (base64, decoded on first use)."""
    import base64
    import json
    rows = []
    for path, data in assets:
        gz, etag = packed(data)
        rows.append(f"[{json.dumps(path)}, {{data: {json.dumps(base64.b64encode(data).decode())}, "
                    f"gz: {json.dumps(base64.b64encode(gz).decode()) if gz else 'null'}, etag: {json.dumps(etag)}}}]")
    return "globalThis.$klar_assets = new Map([\n" + ",\n".join(rows) + "\n]);\n"


# ---------------------------------------------------------------- klar build of a full-stack app

def default_output(backend, target):
    """A project's executable is named after the package (else the folder), and goes next to the project."""
    from .cli import package_output
    folder = os.path.dirname(os.path.abspath(backend))
    out = package_output(os.path.join(os.path.dirname(backend), "main.klar"), target)
    if out:
        return out
    name = os.path.basename(folder) or "app"
    return name + (".mjs" if target == "node" else "")


def cmd_build_app(files, out, release, target, as_json):
    """`klar build server.klar app.klar`: the server, with the browser apps and public files built in."""
    from .cli import cmd_build, cmd_build_js
    split = split_files(files)
    if split is None:
        return 2
    backend, apps = split
    if backend is None:
        say("klar build: these are all browser apps; a full-stack build needs one server too",
            "build each page on its own (klar build app.klar), or all of them for a static host: klar build --static")
        return 2
    assets = collect_assets(backend, apps)
    if assets is None:
        return 1
    target = target or "native"
    out = out or default_output(backend, target)
    if target == "node":
        return cmd_build_js(backend, out, "node", as_json, assets=assets)
    if target != "native":
        say(f"klar build: a server with browser apps builds for native or node, not {target}",
            "klar build --target node server.klar app.klar")
        return 2
    return cmd_build(backend, out, release, False, as_json, assets=assets)


def asset_note(assets):
    if not assets:
        return ""
    size = sum(len(d) for _, d in assets)
    return f", {len(assets)} file(s) built in ({size // 1024 + 1} KB: {', '.join(p for p, _ in assets[:3])}" + \
        (", ..." if len(assets) > 3 else "") + ")"


# ---------------------------------------------------------------- klar build --static

LINK_RE = re.compile(r"""(?:href|src)\s*=\s*["']([^"'#]+)""", re.I)


def cmd_static(files, out_dir, as_json):
    """A folder of plain files for a static host."""
    import shutil
    out_dir = out_dir or "dist"
    split = split_files(files)
    if split is None:
        return 2
    backend, apps = split
    if os.path.exists(out_dir) and os.listdir(out_dir) and not os.path.isfile(os.path.join(out_dir, ".klar-static")):
        say(f"klar build --static: {out_dir}/ exists and was not made by klar build", "choose another folder with -o DIR")
        return 2
    if backend is None:
        return static_apps(apps, out_dir, shutil)
    return static_site(backend, apps, out_dir, shutil)


def fresh_dir(out_dir, shutil):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    with open(os.path.join(out_dir, ".klar-static"), "w") as f:
        f.write("made by klar build --static; rebuilt from scratch on every build\n")


def write_file(out_dir, rel, data):
    p = os.path.join(out_dir, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


def static_apps(apps, out_dir, shutil):
    """Browser apps only: the first one becomes index.html (and 404.html, so paths it handles itself load it on
    hosts that serve 404.html for unknown paths), the others NAME.html; public/ and static/ are copied."""
    pages = []
    for app in apps:
        html = web_page(app)
        if html is None:
            return 1
        pages.append((app, html.encode("utf-8")))
    fresh_dir(out_dir, shutil)
    for i, (app, html) in enumerate(pages):
        name = "index.html" if i == 0 else os.path.splitext(os.path.basename(app))[0] + ".html"
        write_file(out_dir, name, html)
        if i == 0:
            write_file(out_dir, "404.html", html)
    n = 0
    for rel, data in collect_assets(apps[0], []):
        write_file(out_dir, rel.split("/", 1)[1], data)          # public/logo.svg -> logo.svg
        n += 1
    say(f"built {out_dir}/: {len(pages)} page(s) and {n} other file(s); upload the folder to any static host")
    return 0


def static_site(backend, apps, out_dir, shutil):
    """A std.web site: run it, fetch "/" and every page and file it links to, and save them as files."""
    import http.client
    import socket
    import time
    import urllib.parse
    from .cli import cmd_build
    assets = collect_assets(backend, apps)
    if assets is None:
        return 1
    tmp = tempfile.mkdtemp(prefix="klarstatic")
    try:
        exe = os.path.join(tmp, "site")
        if cmd_build(backend, exe, False, False, False, quiet=True, assets=assets) != 0:
            return 1
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = dict(os.environ, PORT=str(port), HOST="127.0.0.1", KLAR_LOG="off")
        proc = subprocess.Popen([exe], env=env, cwd=os.path.dirname(os.path.abspath(backend)),
                                stdout=subprocess.DEVNULL)
        try:
            deadline = time.time() + 15
            while True:
                try:
                    socket.create_connection(("127.0.0.1", port), 0.2).close()
                    break
                except OSError:
                    if proc.poll() is not None or time.time() > deadline:
                        say("klar build --static: the server did not start (see its output above)")
                        return 1
                    time.sleep(0.05)
            fresh_dir(out_dir, shutil)
            todo, seen, saved, problems = ["/"], {"/"}, 0, []
            while todo:
                path = todo.pop(0)
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
                conn.request("GET", path, headers={"host": "localhost"})
                res = conn.getresponse()
                body = res.read()
                conn.close()
                if res.status != 200:
                    problems.append(f"{path}: {res.status}")
                    continue
                ctype = res.getheader("content-type") or ""
                rel = urllib.parse.unquote(path.lstrip("/"))
                if rel == "" or rel.endswith("/"):
                    rel += "index.html"
                elif "." not in rel.rsplit("/", 1)[-1] and ctype.startswith("text/html"):
                    rel += "/index.html"           # /about -> about/index.html: every static host serves that
                if ".." in rel.split("/"):
                    continue
                write_file(out_dir, rel, body)
                saved += 1
                if ctype.startswith("text/html") or ctype.startswith("text/css"):
                    for link in LINK_RE.findall(body.decode("utf-8", "replace")):
                        u = urllib.parse.urlsplit(urllib.parse.urljoin("http://localhost" + path, link.strip()))
                        if u.scheme not in ("http", "https") or u.netloc != "localhost":
                            continue                # another site, mailto:, data:
                        if u.query:
                            problems.append(f"{u.path}?{u.query}: a query string cannot be a static file; skipped")
                            continue
                        if u.path not in seen:
                            seen.add(u.path)
                            todo.append(u.path)
        finally:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for p in problems:
        say(f"  skipped {p}")
    say(f"built {out_dir}/: {saved} file(s) from the pages linked from /; upload the folder to any static host")
    return 1 if saved == 0 else 0


# ---------------------------------------------------------------- klar build --docker

DOCKERFILE = """\
# Written by `klar build --docker`. It builds the app into one executable and runs it as an unprivileged user,
# with its data (SQLite files and anything else it writes to relative paths) in the volume /data.
#
#   docker build -t {name} .
#   docker run -p 8080:8080 -v {name}-data:/data {name}        then open http://localhost:8080
#
# Put a reverse proxy with TLS in front of it in production (DEPLOY.md in the Klar repository has examples).

FROM ubuntu:24.04 AS build
RUN apt-get update && apt-get install -y --no-install-recommends \\
      ca-certificates git python3 gcc libc6-dev libgc-dev libsqlite3-dev libpcre2-dev libssl-dev \\
    && rm -rf /var/lib/apt/lists/*
# The Klar compiler, pinned to the commit this file was written with.
ARG KLAR_REF={ref}
RUN git clone --filter=blob:none https://github.com/arnoac/klar /opt/klar && git -C /opt/klar checkout --detach "$KLAR_REF"
WORKDIR /src
COPY . .
RUN /opt/klar/klarc build --release {files} -o /app

FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libgc1 libsqlite3-0 libpcre2-8-0 libssl3t64 \\
    && rm -rf /var/lib/apt/lists/* \\
    && useradd --system --uid 10001 --home-dir /data app && mkdir /data && chown app /data
COPY --from=build /app /usr/local/bin/app
USER app
WORKDIR /data
VOLUME /data
ENV HOST=0.0.0.0 PORT=8080
EXPOSE 8080
# docker stop sends SIGTERM: the server finishes the requests in flight, then exits
ENTRYPOINT ["/usr/local/bin/app"]
"""

DOCKERIGNORE = """\
.git
.klar
node_modules
dist
*.db
*.db-*
*.sqlite
*.sqlite3
{name}
"""


def klar_ref():
    """The commit of this Klar checkout (what the Dockerfile pins), else the release tag."""
    from . import __version__
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.isdir(os.path.join(root, ".git")):
        try:
            r = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", r.stdout.strip()):
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return "v" + __version__


def cmd_docker(files):
    """Write Dockerfile and .dockerignore next to the server (never over existing ones)."""
    split = split_files(files)
    if split is None:
        return 2
    backend, apps = split
    if backend is None:
        say("klar build --docker: no server among the files; a site without one needs no container",
            "klar build --static, then upload dist/ to any static host")
        return 2
    folder = os.path.dirname(os.path.abspath(backend))
    rel = [os.path.relpath(os.path.abspath(f), folder).replace(os.sep, "/") for f in [backend] + apps]
    name = re.sub(r"[^a-z0-9_.-]", "-", os.path.basename(default_output(backend, "native")).lower()) or "app"
    written = []
    for fname, text in (("Dockerfile", DOCKERFILE.format(name=name, ref=klar_ref(), files=" ".join(rel))),
                        (".dockerignore", DOCKERIGNORE.format(name=name))):
        p = os.path.join(folder, fname)
        if os.path.exists(p):
            say(f"klar build --docker: kept the existing {os.path.relpath(p)}")
            continue
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        written.append(os.path.relpath(p))
    if written:
        say(f"wrote {' and '.join(written)}. Next:\n\n  cd {os.path.relpath(folder)}\n  docker build -t {name} .\n"
            f"  docker run -p 8080:8080 -v {name}-data:/data {name}\n")
    return 0
