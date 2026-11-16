"""Module loader: resolves `use` declarations and merges every module into one program.

`use std.json { ... }` loads klar/std/json.klar; `use dep.x { ... }` loads x.klar from dependency `dep` when the
package's klar.toml names one (see pkg.py); otherwise `use geo.shapes { ... }` loads geo/shapes.klar relative
to the importing file's directory. Each module is loaded once. Test blocks of imported modules are dropped.
"""

import os

from .errors import KlarError
from .syntax import parse, Node

STD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "std")


def resolve_module(path_parts, importer_file, resolver=None):
    if path_parts[0] == "std":
        return os.path.join(STD_DIR, *path_parts[1:]) + ".klar"
    if resolver is not None:
        found = resolver.resolve(path_parts, importer_file)
        if found:
            return found
    base = os.path.dirname(os.path.abspath(importer_file)) if importer_file else os.getcwd()
    return os.path.join(base, *path_parts) + ".klar"


def load_program(src, file=None, overlay=None):
    """Parse the entry source and every module it (transitively) uses.
    Returns a merged Program node with `.modules` (path -> dotted name), or raises KlarError.
    `overlay` maps absolute paths to source text that replaces the file on disk (an editor's unsaved buffers)."""
    from . import pkg
    entry = parse(src, file)
    try:
        resolver = pkg.Resolver(file)
    except pkg.PkgError as ex:
        raise pkg.to_klar_error(ex, file=file)
    entry_key = os.path.abspath(file) if file else "<input>"
    for d in entry.decls:
        d.module = file
    modules = {file: os.path.splitext(os.path.basename(file))[0] if file else "main"}
    decls = list(entry.decls)
    loaded = {entry_key}
    queue = [(d, file) for d in entry.decls if d.kind == "Use"]
    while queue:
        use, importer = queue.pop(0)
        dotted = ".".join(use.path)
        try:
            target = resolve_module(use.path, importer, resolver)
        except pkg.PkgError as ex:
            raise pkg.to_klar_error(ex, use.line, use.col, importer)
        if not os.path.exists(target) and os.path.abspath(target) not in (overlay or {}):
            raise KlarError("E201", f"module `{dotted}` not found (looked for {target})", use.line, use.col,
                            "module paths are relative to the importing file: `use utils.text {...}` loads "
                            "utils/text.klar; standard modules start with `std.`; dependencies are named in "
                            "klar.toml", importer)
        target = os.path.abspath(target)
        use.target_module = file if target == entry_key else target
        if target in loaded:
            continue
        loaded.add(target)
        if overlay and target in overlay:
            mod = parse(overlay[target], target)
        else:
            with open(target, encoding="utf-8") as f:
                mod = parse(f.read(), target)
        modules[target] = dotted
        for d in mod.decls:
            if d.kind == "Test":
                continue
            d.module = target
            decls.append(d)
            if d.kind == "Use":
                queue.append((d, target))
    package = {}
    for m in modules:
        package[m] = "std" if _is_std(m) else resolver.package_of(m)
    rename_private_library_names(decls, modules, package)
    prog = Node("Program", 1, 1, decls=decls)
    prog.modules = modules
    prog.project_root = resolver.project
    prog.entry = file
    return prog


def _is_std(module):
    return isinstance(module, str) and os.path.abspath(module).startswith(STD_DIR + os.sep)


def _rename(node, old, new):
    if isinstance(node, Node):
        if node.kind == "Name" and node.name == old:
            node.name = new
        elif node.kind == "MethodCall" and node.name == old:
            node.name = new
        for v in node.__dict__.values():
            _rename(v, old, new)
    elif isinstance(node, (list, tuple)):
        for x in node:
            _rename(x, old, new)


def _defined(d):
    if d.kind in ("Fn", "Extern", "Struct", "Enum", "ExternType"):
        yield d.name
    if d.kind == "Enum":
        yield from (v.name for v in d.variants)
    if d.kind == "Trait":
        yield from (m.name for m in d.methods)


def rename_private_library_names(decls, modules, package):
    """Top-level names are unique program-wide, but a library (the standard library or a dependency) must not
    take a name away from anyone else. When a library function's name is also defined in another package, the
    library's definition is renamed internally, together with the references to it: inside the library, and in
    each module that imports it, as long as that module does not also see the other definition (then the
    checker reports the clash). `package` maps each module to "std", a dependency name, or None (the project)."""
    pkg_of = lambda m: package.get(m)
    owners = {}
    for d in decls:
        for n in _defined(d):
            owners.setdefault(n, set()).add(pkg_of(d.module))
    defined_in = {}
    for d in decls:
        for n in _defined(d):
            defined_in.setdefault(n, set()).add(d.module)
    imports = {}            # (module, name) -> set of target modules it imports that name from
    for d in decls:
        if d.kind == "Use":
            for n, _ in d.names:
                imports.setdefault((d.module, n), set()).add(getattr(d, "target_module", None))

    def rename_in(module, old, new):
        for o in decls:
            if o.module == module and o.kind in ("Fn", "Impl", "Test"):
                _rename(o.body if o.kind in ("Fn", "Test") else o, old, new)

    for d in decls:
        lib = pkg_of(d.module)
        if lib is None or d.kind not in ("Fn", "Extern") or len(owners.get(d.name, ())) < 2:
            continue
        old, mod = d.name, d.module
        importers = [u for u in decls if u.kind == "Use" and getattr(u, "target_module", None) == mod
                     and pkg_of(u.module) != lib and any(n == old for n, _ in u.names)]
        if any(u.module in defined_in.get(old, ()) or len(imports.get((u.module, old), ())) > 1 for u in importers):
            continue        # a module sees both definitions: a real clash, reported by the checker
        new = ("std_" if lib == "std" else lib + "_") + modules.get(mod, "x").split(".")[-1] + "__" + old
        rename_in(mod, old, new)
        for other in decls:
            if other.kind == "Use" and getattr(other, "target_module", None) == mod and other.module != mod \
                    and any(n == old for n, _ in other.names):
                other.names = [(new if n == old else n, t) for n, t in other.names]
                rename_in(other.module, old, new)
        d.name = new
