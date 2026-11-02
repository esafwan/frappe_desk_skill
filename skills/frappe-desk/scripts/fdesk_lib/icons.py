"""Icon lookup over the union of every installed app's sprites. Stdlib only."""

from __future__ import annotations

import ast
import difflib
import json
import os
import re
from pathlib import Path

from .common import App, FdeskError, Result

_SYMBOL_RE = re.compile(r"<symbol\b[^>]*?\bid=\"([^\"]+)\"")
_CONTEXTS = ("sidebar", "workspace", "dock")
_MEM: dict = {}  # sprite path -> (mtime_ns, size, frozenset(ids))


def find_bench(app: App | None = None) -> Path | None:
	"""FDESK_BENCH, else the nearest ancestor of the app that holds both apps/ and sites/."""
	env = os.environ.get("FDESK_BENCH")
	if env:
		p = Path(env).expanduser()
		return p.resolve() if (p / "apps").is_dir() else None
	if app is None:
		try:
			app = App.find()
		except FdeskError:
			return None
	for cand in Path(app.root).resolve().parents:
		if (cand / "apps").is_dir() and (cand / "sites").is_dir():
			return cand
	return None


def installed_packages(app: App | None = None, bench: Path | None = None) -> list[tuple[str, Path]]:
	"""[(app name, package dir)] for the current app (first) and every app in the bench."""
	out: dict[str, Path] = {}
	if app is not None:
		out[app.name] = Path(app.package)
	bench = bench or find_bench(app)
	if bench and (bench / "apps").is_dir():
		try:
			entries = sorted(os.scandir(bench / "apps"), key=lambda e: e.name)
		except OSError:
			entries = []
		for e in entries:
			if not e.is_dir(follow_symlinks=True):
				continue
			for cand in (Path(e.path) / e.name, Path(e.path)):
				if (cand / "hooks.py").is_file() and (cand / "modules.txt").is_file():
					out.setdefault(cand.name, cand)
					break
	return list(out.items())


def _hook_icons(hooks: Path) -> list[str]:
	try:
		tree = ast.parse(hooks.read_text(encoding="utf-8"))
	except (OSError, SyntaxError, ValueError):
		return []
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "app_include_icons" for t in node.targets):
			try:
				v = ast.literal_eval(node.value)
			except ValueError:
				return []
			return [v] if isinstance(v, str) else [x for x in v if isinstance(x, str)]
	return []


def _sprite_files(packages: list[tuple[str, Path]]) -> tuple[list[Path], list[Path]]:
	"""(sprite svg files, aliases.json files). Asset URL /assets/<app>/x -> <app package>/public/x."""
	pkgs = dict(packages)
	svgs: list[Path] = []
	aliases: list[Path] = []

	def add(p: Path):
		if p.is_file() and p not in svgs:
			svgs.append(p)

	for name, pkg in packages:
		for url in _hook_icons(pkg / "hooks.py"):
			m = re.match(r"^/?assets/([^/]+)/(.+)$", url)
			if m and m.group(1) in pkgs:
				add(pkgs[m.group(1)] / "public" / m.group(2))
			elif not url.startswith("/"):
				add(pkg / "public" / url)
		if name == "frappe":
			icons = pkg / "public" / "icons"
			for rel in ("lucide/icons.svg", "timeless/icons.svg", "espresso/icons.svg", "module-icons.svg"):
				add(icons / rel)
			if (icons / "lucide" / "aliases.json").is_file():
				aliases.append(icons / "lucide" / "aliases.json")
	return svgs, aliases


def _ids(path: Path) -> frozenset:
	try:
		st = path.stat()
		hit = _MEM.get(str(path))
		if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
			return hit[2]
		ids = frozenset(_SYMBOL_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
	except OSError:
		return frozenset()
	_MEM[str(path)] = (st.st_mtime_ns, st.st_size, ids)
	return ids


def _norm(sym_id: str) -> str:
	return sym_id[5:] if sym_id.startswith("icon-") else sym_id


def load_union(app: App | None = None) -> tuple[set[str], dict[str, str]]:
	"""(valid icon names, alias map). Names are sprite ids without the 'icon-' prefix (raw ids kept too)."""
	if app is None:
		try:
			app = App.find()
		except FdeskError:
			app = None
	packages = installed_packages(app)
	svgs, alias_files = _sprite_files(packages)
	names: set[str] = set()
	for f in svgs:
		for i in _ids(f):
			names.add(_norm(i))
			names.add(i)
	aliases: dict[str, str] = {}
	for f in alias_files:
		try:
			data = json.loads(f.read_text(encoding="utf-8"))
		except (OSError, ValueError):
			continue
		if isinstance(data, dict):
			aliases.update({k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)})
	return names, aliases


def exists(name: str, context: str = "sidebar", app=None) -> bool | None:
	"""context: sidebar|workspace|dock. Union of all installed apps' app_include_icons sprites + lucide aliases.
	'-duotone' names are valid only for context == 'dock'. None when no sprite could be found (warn, not fail)."""
	if context not in _CONTEXTS:
		raise FdeskError(f"unknown icon context {context!r}; expected one of {list(_CONTEXTS)}")
	names, aliases = load_union(app)
	if not names:
		return None
	n = (name or "").strip()
	if not n:
		return False
	cands = [n, _norm(n)]
	if aliases.get(_norm(n)):
		cands.append(aliases[_norm(n)])
	for c in cands:
		if c in names:
			if c.endswith("-duotone") and context != "dock":
				continue
			return True
	return False


def _score(q: str, tokens: list[str], n: str) -> float | None:
	if n == q:
		return 0
	if n.startswith(q):
		return 1 + len(n) / 1000
	parts = re.split(r"[-_]", n)
	if q in parts:
		return 2 + len(n) / 1000
	if q in n:
		return 3 + n.index(q) / 100 + len(n) / 10000
	if len(tokens) > 1 and all(t in n for t in tokens):
		return 4 + len(n) / 1000
	return None


def search(query: str, limit: int = 20, app=None) -> list[str]:
	"""Rank: exact, prefix, whole word, substring, all-tokens, then fuzzy (difflib). Duotone names sort last on ties."""
	names, aliases = load_union(app)
	q = (query or "").strip().lower().replace(" ", "-")
	if not q:
		return []
	tokens = [t for t in re.split(r"[-_\s]+", (query or "").lower()) if t]
	pool = {_norm(n) for n in names} | {a for a, t in aliases.items() if t in names or "icon-" + t in names}
	scored = []
	for n in pool:
		s = _score(q, tokens, n)
		if s is not None:
			scored.append((s + (0.5 if n.endswith("-duotone") else 0), n))
	if len(scored) < limit:
		seen = {n for _, n in scored}
		for n in difflib.get_close_matches(q, [p for p in pool if p not in seen], n=limit, cutoff=0.6):
			scored.append((10 - difflib.SequenceMatcher(None, q, n).ratio(), n))
	scored.sort(key=lambda t: (t[0], t[1]))
	return [n for _, n in scored[: max(limit, 0)]]


# ---- CLI -------------------------------------------------------------------

def _app(args) -> App | None:
	try:
		return App.find(getattr(args, "app", None))
	except FdeskError:
		return None


def _emit(args, text: str) -> None:
	if not getattr(args, "json", False):
		print(text)


def cmd_search(args) -> Result:
	res = Result()
	hits = search(args.query, args.limit, _app(args))
	res.info["results"] = hits
	if not load_union(_app(args))[0]:
		res.warnings.append("no icon sprites found (no bench / frappe checkout); set FDESK_BENCH")
	_emit(args, "\n".join(hits))
	return res


def cmd_check(args) -> Result:
	res = Result()
	v = exists(args.name, args.context, _app(args))
	res.info.update({"name": args.name, "context": args.context, "valid": v})
	if v is None:
		res.warnings.append("no icon sprites found (no bench / frappe checkout); cannot verify")
		_emit(args, "unknown (no sprites found)")
	elif v:
		_emit(args, f"ok: {args.name} ({args.context})")
	else:
		hint = search(args.name, 5, _app(args))
		res.info["suggestions"] = hint
		res.errors.append(f"unknown icon {args.name!r} for context {args.context}" + (f"; did you mean: {', '.join(hint)}" if hint else ""))
		if args.name.endswith("-duotone") and args.context != "dock":
			res.errors[-1] += " (-duotone icons are valid only in the dock)"
	return res


def cmd_list(args) -> Result:
	res = Result()
	names, aliases = load_union(_app(args))
	pool = sorted({_norm(n) for n in names} | {a for a, t in aliases.items() if t in names or "icon-" + t in names})
	if args.prefix:
		pool = [n for n in pool if n.startswith(args.prefix)]
	res.info["count"] = len(pool)
	res.info["icons"] = pool
	if not names:
		res.warnings.append("no icon sprites found (no bench / frappe checkout); set FDESK_BENCH")
	_emit(args, "\n".join(pool))
	return res


def register(sub):
	g = sub.add_parser("icons", help="search/check icons against the union of installed apps' sprites (read-only)")
	gs = g.add_subparsers(dest="cmd", required=True)
	p = gs.add_parser("search", help="fuzzy search icon names")
	p.add_argument("query")
	p.add_argument("--limit", type=int, default=20)
	p.set_defaults(func=cmd_search)
	p = gs.add_parser("check", help="validate an icon name (exit 1 when unknown)")
	p.add_argument("name")
	p.add_argument("--context", choices=list(_CONTEXTS), default="sidebar")
	p.set_defaults(func=cmd_check)
	p = gs.add_parser("list", help="list icon names")
	p.add_argument("--prefix", default=None)
	p.set_defaults(func=cmd_list)
