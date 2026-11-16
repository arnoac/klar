"""`klar audit`: check a project's dependencies, and Klar itself, against known vulnerabilities.

What is checked:
  - Python packages pinned in klar.lock ([[python]] entries)          -> OSV database, ecosystem PyPI
  - npm packages in package-lock.json (every package, incl. transitive) -> OSV database, ecosystem npm
  - git dependencies pinned in klar.lock ([[package]] commit)           -> OSV database, by commit
  - the Klar toolchain running the command                             -> Klar's published security advisories

It also warns about dependencies that are not pinned or verified (no lock entry, no hashes).

Exit status: 0 nothing known, 1 vulnerabilities found, 2 the check could not be completed (offline, bad lock
file). CI should treat 2 as a failure too, so a network problem never reads as "all clear".

Sources can be pointed elsewhere (a mirror, or a test server): KLAR_OSV_URL (default https://api.osv.dev) and
KLAR_ADVISORIES_URL (default: the GitHub security advisories of the Klar repository).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

from . import __version__

OSV_URL = "https://api.osv.dev"
ADVISORIES_URL = "https://api.github.com/repos/arnoac/klar/security-advisories?state=published&per_page=100"
SEVERITY_ORDER = ["critical", "high", "moderate", "medium", "low", "unknown"]


class AuditError(Exception):
    pass


# ---------------------------------------------------------------------------------------------- what to check

def collect(root):
    """(packages, warnings) for the project at `root`. A package is a dict with ecosystem, name, version (or
    commit for git) and `direct` (named in klar.toml rather than pulled in by another package)."""
    from . import pkg
    pkgs, warnings = [], []
    man = None
    if os.path.isfile(os.path.join(root, pkg.MANIFEST)):
        try:
            man = pkg.Manifest(root)
        except pkg.PkgError as ex:
            raise AuditError(str(ex))
    lock_path = os.path.join(root, pkg.LOCK)
    lock = {}
    if os.path.exists(lock_path):
        try:
            lock = pkg._toml(lock_path)
        except pkg.PkgError as ex:
            raise AuditError(str(ex))
    for e in lock.get("python", []):
        pkgs.append({"ecosystem": "PyPI", "name": e["name"], "version": e["version"],
                     "direct": bool(e.get("direct")) or bool(man and e["name"] in man.python)})
        if not e.get("hashes") and not e.get("sha256"):
            warnings.append(f"Python package {e['name']} {e['version']} has no hashes in klar.lock; run `klar install` "
                            "to record them, so a changed download is refused")
    for e in lock.get("package", []):
        pkgs.append({"ecosystem": "git", "name": e["name"], "commit": e.get("commit", ""), "git": e.get("git", ""),
                     "direct": bool(man and e["name"] in man.deps)})
    if man is not None:
        locked_py = {e["name"] for e in lock.get("python", [])}
        locked_git = {e["name"] for e in lock.get("package", [])}
        for name in man.python:
            if name not in locked_py:
                warnings.append(f"Python package {name} is in klar.toml but not in klar.lock: run `klar install`")
        for name, spec in man.deps.items():
            if "git" in spec and name not in locked_git:
                warnings.append(f"dependency {name} is not pinned in klar.lock: run `klar install`")
    npm_direct = set(getattr(man, "npm", {}) or {}) if man is not None else set()
    pl = os.path.join(root, "package-lock.json")
    if os.path.exists(pl):
        try:
            with open(pl, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as ex:
            raise AuditError(f"{pl}: {ex}")
        for key, e in (data.get("packages") or {}).items():
            if not key or "node_modules/" not in key or e.get("link"):
                continue
            name = e.get("name") or key.rsplit("node_modules/", 1)[1]
            if not e.get("version"):
                continue
            pkgs.append({"ecosystem": "npm", "name": name, "version": e["version"],
                         "direct": key == "node_modules/" + name and name in npm_direct})
            if not e.get("integrity") and not e.get("resolved", "").startswith("file:"):
                warnings.append(f"npm package {name} {e['version']} has no integrity hash in package-lock.json")
    elif npm_direct:
        warnings.append("klar.toml names npm packages but there is no package-lock.json: run `klar install`")
    return pkgs, warnings


# ---------------------------------------------------------------------------------------------- sources

def _http_json(url, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"content-type": "application/json", "accept": "application/json",
                                          "user-agent": "klar-audit"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as ex:
        raise AuditError(f"could not reach {url.split('?')[0]}: {getattr(ex, 'reason', ex)}")


def query_osv(pkgs):
    """OSV advisories per package: a list (same order as pkgs) of lists of vulnerability records."""
    base = (os.environ.get("KLAR_OSV_URL") or OSV_URL).rstrip("/")
    queries = []
    for p in pkgs:
        if p["ecosystem"] == "git":
            queries.append({"commit": p["commit"]})
        else:
            queries.append({"package": {"name": p["name"], "ecosystem": p["ecosystem"]}, "version": p["version"]})
    ids = []
    for i in range(0, len(queries), 1000):                # OSV's batch limit
        res = _http_json(base + "/v1/querybatch", {"queries": queries[i:i + 1000]})
        ids += [[v["id"] for v in (r.get("vulns") or [])] for r in res.get("results", [])]
    if len(ids) != len(pkgs):
        raise AuditError("the vulnerability database gave an incomplete answer")
    details = {}
    for vid in sorted({v for vs in ids for v in vs}):
        details[vid] = _http_json(f"{base}/v1/vulns/{vid}")
    return [[details[v] for v in vs] for vs in ids]


def _severity(vuln):
    s = (vuln.get("database_specific") or {}).get("severity")
    if isinstance(s, str) and s:
        return s.lower()
    for a in vuln.get("affected", []):
        s = (a.get("database_specific") or {}).get("severity") or (a.get("ecosystem_specific") or {}).get("severity")
        if isinstance(s, str) and s:
            return s.lower()
    return "unknown"


def _fixed(vuln, p):
    """The lowest version that fixes `vuln` for package `p`, or None."""
    fixes = []
    for a in vuln.get("affected", []):
        pk = a.get("package") or {}
        if p["ecosystem"] != "git" and (pk.get("name", "").lower() != p["name"].lower()):
            continue
        for r in a.get("ranges", []):
            for ev in r.get("events", []):
                if "fixed" in ev:
                    fixes.append(ev["fixed"])
    if not fixes:
        return None
    return sorted(fixes, key=_vkey)[0]


def _vkey(v):
    """A sort key for version strings like 1.2.10, v2.0.0-rc.1 (numbers compare as numbers)."""
    v = v.lstrip("vV")
    main, _, pre = v.partition("-")
    nums = [int(x) if x.isdigit() else 0 for x in re.split(r"[.+]", main)]
    return (nums + [0] * (4 - len(nums)), 0 if pre else 1, pre)


def _in_range(version, spec):
    """Whether `version` satisfies a GitHub advisory range such as "< 0.2.0" or ">= 0.1.0, < 0.1.3"."""
    for part in [x.strip() for x in spec.split(",") if x.strip()]:
        m = re.match(r"^(<=|>=|<|>|=)?\s*v?([0-9][0-9A-Za-z.+-]*)$", part)
        if not m:
            return False
        op, want = m.group(1) or "=", _vkey(m.group(2))
        have = _vkey(version)
        ok = {"<": have < want, "<=": have <= want, ">": have > want, ">=": have >= want, "=": have == want}[op]
        if not ok:
            return False
    return True


def query_klar(version):
    """Published advisories for the Klar toolchain that affect `version`."""
    url = os.environ.get("KLAR_ADVISORIES_URL") or ADVISORIES_URL
    data = _http_json(url)
    out = []
    for adv in data if isinstance(data, list) else []:
        for v in adv.get("vulnerabilities") or []:
            if _in_range(version, v.get("vulnerable_version_range") or ""):
                out.append({"id": adv.get("ghsa_id") or adv.get("cve_id") or "?", "summary": adv.get("summary", ""),
                            "severity": (adv.get("severity") or "unknown").lower(),
                            "fixed": v.get("patched_versions") or None,
                            "url": adv.get("html_url", "")})
                break
    return out


# ---------------------------------------------------------------------------------------------- the command

def _fix_hint(p, fixed):
    if p["ecosystem"] == "PyPI":
        return f"klar add --python {p['name']}=={fixed}" if fixed and p["direct"] else \
            "upgrade the package that depends on it" if fixed else "no fixed version yet: consider removing it"
    if p["ecosystem"] == "npm":
        return f"klar add --npm {p['name']}@{fixed}" if fixed and p["direct"] else \
            "upgrade the npm package that depends on it" if fixed else "no fixed version yet: consider removing it"
    return f"klar update {p['name']}"


def audit(root):
    pkgs, warnings = collect(root)
    findings = []
    vulns = query_osv(pkgs) if pkgs else []
    for p, vs in zip(pkgs, vulns):
        for v in vs:
            fixed = _fixed(v, p)
            aliases = [a for a in v.get("aliases", []) if a.startswith("CVE-")]
            findings.append({
                "ecosystem": p["ecosystem"], "name": p["name"], "version": p.get("version") or p.get("commit", "")[:12],
                "id": v.get("id"), "aliases": aliases, "severity": _severity(v),
                "summary": v.get("summary") or (v.get("details") or "").split("\n")[0][:160],
                "fixed": fixed, "fix": _fix_hint(p, fixed), "url": f"https://osv.dev/vulnerability/{v.get('id')}",
            })
    for adv in query_klar(__version__):
        findings.append({"ecosystem": "klar", "name": "klar", "version": __version__, "id": adv["id"], "aliases": [],
                         "severity": adv["severity"], "summary": adv["summary"], "fixed": adv["fixed"],
                         "fix": "upgrade Klar: git pull in your Klar checkout, then run `klar audit` again",
                         "url": adv["url"]})
    findings.sort(key=lambda f: (SEVERITY_ORDER.index(f["severity"]) if f["severity"] in SEVERITY_ORDER else 9,
                                 f["ecosystem"], f["name"]))
    counts = {}
    for p in pkgs:
        counts[p["ecosystem"]] = counts.get(p["ecosystem"], 0) + 1
    return {"klar": __version__, "checked": counts, "findings": findings, "warnings": warnings}


def cmd_audit(as_json=False, path=None):
    from . import pkg
    root = pkg.find_root(path or os.getcwd()) or os.path.abspath(path or os.getcwd())
    try:
        report = audit(root)
    except AuditError as ex:
        if as_json:
            print(json.dumps({"error": str(ex)}))
        else:
            print(f"klar audit: {ex}", file=sys.stderr)
            print("  the check did not complete, so nothing is known to be safe", file=sys.stderr)
        return 2
    if as_json:
        print(json.dumps(report, indent=2))
        return 1 if report["findings"] else 0
    checked = ", ".join(f"{n} {eco}" for eco, n in sorted(report["checked"].items())) or "no dependencies"
    print(f"klar audit: checked {checked} and Klar {report['klar']}")
    for w in report["warnings"]:
        print(f"  warning: {w}")
    for f in report["findings"]:
        ids = " / ".join([f["id"]] + f["aliases"])
        print(f"\n  {f['severity'].upper():9} {f['name']} {f['version']} ({f['ecosystem']}): {ids}")
        if f["summary"]:
            print(f"            {f['summary']}")
        print(f"            fix: {f['fix']}" + (f" (fixed in {f['fixed']})" if f["fixed"] else ""))
        print(f"            {f['url']}")
    n = len(report["findings"])
    print(f"\n{n} known vulnerabilit{'y' if n == 1 else 'ies'}" if n else "no known vulnerabilities")
    return 1 if n else 0
