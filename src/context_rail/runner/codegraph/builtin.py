"""The builtin code graph provider: standard library only, in process.

It reads the committed tree (``git ls-tree``), parses Python with ``ast`` and
finds JS/TS imports with a regular expression. Calls and inheritance are
resolved by name against imports and the module's own definitions, so they are
marked ``inferred``. Anything it cannot read is skipped and counted, never
fatal.
"""

from __future__ import annotations

import ast
import posixpath
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from context_rail.model.codegraph import Edge, Node, Snapshot
from context_rail.runner.git import ls_files

__all__ = ["BUILTIN_VERSION", "MAX_FILE_BYTES", "MAX_SKIPPED_LISTED", "extract"]

BUILTIN_VERSION = "1"
MAX_FILE_BYTES = 1_000_000
MAX_SKIPPED_LISTED = 200

_PY = (".py",)
_JS = (".ts", ".tsx", ".js", ".jsx", ".mjs")
_JS_IMPORT = re.compile(
    r"""(?:import|export)\s[^'"]*?from\s*['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\s*['"]([^'"]+)['"]"""
)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"^[ \t]*//.*$", re.MULTILINE)
_SELF_NAMES = ("self", "cls")


@dataclass
class _Module:
    path: str
    defs: dict[str, str] = field(default_factory=dict)
    imports: list[tuple[int, str | None, tuple[tuple[str, str | None], ...], bool]] = field(
        default_factory=list
    )
    calls: list[tuple[str, str, str | None, str | None]] = field(default_factory=list)
    bases: list[tuple[str, ast.expr]] = field(default_factory=list)


@dataclass
class _Build:
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[tuple[str, str, str], Edge] = field(default_factory=dict)
    modules: dict[str, _Module] = field(default_factory=dict)
    scripts: dict[str, list[str]] = field(default_factory=dict)
    methods: dict[str, dict[str, str]] = field(default_factory=lambda: defaultdict(dict))
    skipped: list[str] = field(default_factory=list)
    parsed_python: bool = False

    def add_node(self, node: Node) -> bool:
        if node.id in self.nodes:
            return False
        self.nodes[node.id] = node
        return True

    def add_edge(self, src: str, dst: str, rel: str, confidence: str) -> None:
        if src == dst:
            return
        self.edges.setdefault((src, dst, rel), Edge(src, dst, rel, confidence))


def extract(worktree: Path, commit: str, *, deadline: float) -> Snapshot:
    """The snapshot of the committed tree in ``worktree``.

    Raises ``TimeoutError`` when ``time.monotonic()`` passes ``deadline``.
    """
    worktree = Path(worktree)
    paths = sorted(ls_files(worktree, worktree))
    build = _Build()
    for path in paths:
        _check(deadline)
        _read_file(worktree, path, build)
    parsed = set(build.modules) | set(build.scripts)
    package_index = _PackageIndex(sorted(build.modules))
    for path, module in sorted(build.modules.items()):
        _check(deadline)
        _link_python(build, module, package_index)
    for path, specs in sorted(build.scripts.items()):
        _check(deadline)
        for spec in specs:
            target = _resolve_script_import(path, spec, parsed)
            if target is not None:
                build.add_edge(path, target, "imports", "extracted")
    skipped_total = len(build.skipped)
    return Snapshot(
        provider="builtin",
        provider_version=BUILTIN_VERSION,
        commit=commit,
        built_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        calls=build.parsed_python,
        lines=any(node.start is not None for node in build.nodes.values()),
        files_listed=len(paths),
        files_parsed=len(paths) - skipped_total,
        skipped=tuple(build.skipped[:MAX_SKIPPED_LISTED]),
        nodes=tuple(build.nodes.values()),
        edges=tuple(build.edges.values()),
    )


def _check(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise TimeoutError("code graph extraction passed its deadline")


def _read_file(worktree: Path, path: str, build: _Build) -> None:
    suffix = posixpath.splitext(path)[1].lower()
    if ":" in path:
        build.skipped.append(path)
        return
    if suffix not in _PY + _JS:
        build.add_node(Node(path, "file", posixpath.basename(path), path, None, None))
        return
    source = _source(worktree / path)
    if source is None:
        build.skipped.append(path)
        return
    if suffix in _PY:
        module = _parse_python(path, source, build)
        if module is None:
            build.skipped.append(path)
            return
        build.modules[path] = module
        build.parsed_python = True
        return
    build.add_node(Node(path, "file", posixpath.basename(path), path, None, None))
    build.scripts[path] = _script_imports(source)


def _source(file: Path) -> str | None:
    try:
        if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
            return None
        return file.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _parse_python(path: str, source: str, build: _Build) -> _Module | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    build.add_node(Node(path, "file", posixpath.basename(path), path, None, None))
    module = _Module(path)
    _Collector(module, build).visit(tree)
    return module


class _Collector(ast.NodeVisitor):
    """Collects symbols, imports, calls and bases from one module."""

    def __init__(self, module: _Module, build: _Build) -> None:
        self._module = module
        self._build = build
        self._parent = module.path
        self._parent_is_class = False
        self._prefix = ""
        self._src = module.path
        self._in_function = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for keyword in node.keywords:
            self.visit(keyword.value)
        if self._in_function:
            for statement in node.body:
                self.visit(statement)
            return
        qualified = self._prefix + node.name
        symbol = f"{self._module.path}:{qualified}"
        if not self._build.add_node(
            Node(symbol, "class", node.name, self._module.path, _first_line(node), node.end_lineno)
        ):
            return
        self._build.add_edge(self._parent, symbol, "contains", "extracted")
        if not self._prefix:
            self._module.defs[node.name] = symbol
        for base in node.bases:
            self._module.bases.append((symbol, base))
        saved = (self._parent, self._parent_is_class, self._prefix, self._src)
        self._parent, self._parent_is_class = symbol, True
        self._prefix, self._src = qualified + ".", symbol
        for statement in node.body:
            self.visit(statement)
        self._parent, self._parent_is_class, self._prefix, self._src = saved

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        if self._in_function:
            for statement in node.body:
                self.visit(statement)
            return
        qualified = self._prefix + node.name
        symbol = f"{self._module.path}:{qualified}"
        kind = "method" if self._parent_is_class else "function"
        if not self._build.add_node(
            Node(symbol, kind, node.name, self._module.path, _first_line(node), node.end_lineno)
        ):
            return
        self._build.add_edge(self._parent, symbol, "contains", "extracted")
        if self._parent_is_class:
            self._build.methods[self._parent][node.name] = symbol
        elif not self._prefix:
            self._module.defs[node.name] = symbol
        saved_src = self._src
        self._src = symbol
        self._in_function += 1
        for statement in node.body:
            self.visit(statement)
        self._in_function -= 1
        self._src = saved_src

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Import(self, node: ast.Import) -> None:
        names = tuple((alias.name, alias.asname) for alias in node.names)
        self._module.imports.append((0, None, names, False))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = tuple((alias.name, alias.asname) for alias in node.names)
        self._module.imports.append((node.level, node.module, names, True))

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            self._module.calls.append((self._src, func.id, None, None))
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner = self._parent if self._parent_is_class else None
            self._module.calls.append((self._src, func.value.id, func.attr, owner))
        self.generic_visit(node)


def _first_line(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    return min([node.lineno, *(d.lineno for d in node.decorator_list)])


class _PackageIndex:
    """Dotted module names for Python files, resolved without importing anything."""

    def __init__(self, paths: list[str]) -> None:
        self.paths = set(paths)
        self._names: dict[str, list[str]] = defaultdict(list)
        for path in paths:
            for name in self._dotted(path):
                self._names[name].append(path)

    def _dotted(self, path: str) -> set[str]:
        directory, filename = posixpath.split(path)
        stem = posixpath.splitext(filename)[0]
        full = [part for part in directory.split("/") if part]
        if stem != "__init__":
            full.append(stem)
        names = {".".join(full)} if full else set()
        parts = [] if stem == "__init__" else [stem]
        climbing = directory
        in_package = False
        while climbing and posixpath.join(climbing, "__init__.py") in self.paths:
            parts.insert(0, posixpath.basename(climbing))
            climbing = posixpath.dirname(climbing)
            in_package = True
        if in_package and parts:
            names.add(".".join(parts))
        return names

    def module_file(self, importer: str, level: int, module: str | None) -> str | None:
        parts = module.split(".") if module else []
        if level:
            base = posixpath.dirname(importer)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            return self._in_directory(base, parts)
        dotted = ".".join(parts)
        candidates = self._names.get(dotted)
        if candidates:
            return _closest(importer, candidates)
        return self._in_directory(posixpath.dirname(importer), parts)

    def _in_directory(self, base: str, parts: list[str]) -> str | None:
        joined = posixpath.join(base, *parts) if parts else base
        for candidate in (joined + ".py", posixpath.join(joined, "__init__.py")):
            if candidate in self.paths and (parts or candidate.endswith("__init__.py")):
                return candidate
        return None


def _closest(importer: str, candidates: list[str]) -> str:
    def shared(path: str) -> int:
        count = 0
        for left, right in zip(importer.split("/"), path.split("/"), strict=False):
            if left != right:
                break
            count += 1
        return count

    return min(candidates, key=lambda path: (-shared(path), path))


def _link_python(build: _Build, module: _Module, index: _PackageIndex) -> None:
    bindings: dict[str, tuple[str, str]] = {}
    for level, name, names, is_from in module.imports:
        if not is_from:
            for dotted, alias in names:
                target = index.module_file(module.path, 0, dotted)
                if target is None:
                    continue
                build.add_edge(module.path, target, "imports", "extracted")
                if alias is not None:
                    bindings.setdefault(alias, ("mod", target))
                elif "." not in dotted:
                    bindings.setdefault(dotted, ("mod", target))
            continue
        target = index.module_file(module.path, level, name)
        if target is not None:
            build.add_edge(module.path, target, "imports", "extracted")
        for imported, alias in names:
            bound = alias or imported
            sub_name = f"{name}.{imported}" if name else imported
            sub = index.module_file(module.path, level, sub_name)
            if sub is not None and sub != target:
                build.add_edge(module.path, sub, "imports", "extracted")
            found = _exported(build, index, target, imported)
            if found is not None:
                bindings.setdefault(bound, ("sym", found))
            elif sub is not None:
                bindings.setdefault(bound, ("mod", sub))
    for src, root, attr, owner in module.calls:
        target = _call_target(build, module, bindings, root, attr, owner)
        if target is not None:
            build.add_edge(src, target, "calls", "inferred")
    for class_id, base in module.bases:
        target = _base_target(build, module, bindings, base)
        if target is not None:
            build.add_edge(class_id, target, "inherits", "inferred")


def _exported(
    build: _Build, index: _PackageIndex, path: str | None, name: str, depth: int = 2
) -> str | None:
    """The symbol id ``name`` names inside module ``path``, following re-exports."""
    module = build.modules.get(path) if path else None
    if module is None:
        return None
    if name in module.defs:
        return module.defs[name]
    if depth <= 1:
        return None
    for level, source, names, is_from in module.imports:
        if not is_from:
            continue
        for imported, alias in names:
            if (alias or imported) != name:
                continue
            origin = index.module_file(module.path, level, source)
            return _exported(build, index, origin, imported, depth - 1)
    return None


def _call_target(
    build: _Build,
    module: _Module,
    bindings: dict[str, tuple[str, str]],
    root: str,
    attr: str | None,
    owner: str | None,
) -> str | None:
    if attr is None:
        if root in module.defs:
            return module.defs[root]
        bound = bindings.get(root)
        return bound[1] if bound is not None and bound[0] == "sym" else None
    if root in _SELF_NAMES and owner is not None:
        return build.methods.get(owner, {}).get(attr)
    bound = bindings.get(root)
    if bound is not None and bound[0] == "mod":
        other = build.modules.get(bound[1])
        return None if other is None else other.defs.get(attr)
    class_id = module.defs.get(root) if bound is None else bound[1]
    if class_id is not None:
        return build.methods.get(class_id, {}).get(attr)
    return None


def _base_target(
    build: _Build, module: _Module, bindings: dict[str, tuple[str, str]], base: ast.expr
) -> str | None:
    if isinstance(base, ast.Name):
        return _call_target(build, module, bindings, base.id, None, None)
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        bound = bindings.get(base.value.id)
        if bound is not None and bound[0] == "mod":
            other = build.modules.get(bound[1])
            return None if other is None else other.defs.get(base.attr)
    return None


def _script_imports(source: str) -> list[str]:
    cleaned = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))
    found: list[str] = []
    for match in _JS_IMPORT.finditer(cleaned):
        spec = next((group for group in match.groups() if group), None)
        if spec is not None and spec.startswith("."):
            found.append(spec)
    return found


def _resolve_script_import(importer: str, spec: str, known: set[str]) -> str | None:
    base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    if base.startswith("..") or base == ".":
        return None
    stem, extension = posixpath.splitext(base)
    candidates = [base]
    if extension in _JS:
        candidates.extend(stem + other for other in _JS if other != extension)
    candidates.extend(base + other for other in _JS)
    candidates.extend(posixpath.join(base, "index" + other) for other in _JS)
    for candidate in candidates:
        if candidate in known and candidate != importer:
            return candidate
    return None
