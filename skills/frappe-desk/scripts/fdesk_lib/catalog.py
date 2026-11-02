"""Self-healing catalog of linkable entities across the app and every installed bench app.

Cache: $FDESK_CACHE_DIR or ~/.cache/fdesk/<hash of app path>/catalog.json (never inside the app repo). Always rebuilt when missing, corrupt, wrong schema or
fingerprint-stale; never trusted blindly; written atomically (temp file + rename).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .common import App, FdeskError, Result

SCHEMA = 1
CACHE_DIR = ".fdesk-cache"
CACHE_FILE = "catalog.json"

# folder name -> catalog kind
KIND_DIRS = {
	"doctype": "doctype", "report": "report", "page": "page", "workspace": "workspace",
	"sidebar": "sidebar", "workspace_sidebar": "sidebar", "dashboard": "dashboard",
	"dashboard_chart": "chart", "number_card": "number_card", "custom_html_block": "custom_block",
}
KINDS = sorted(set(KIND_DIRS.values()))
ALIASES = {"dashboard_chart": "chart", "custom_html_block": "custom_block", "block": "custom_block", "number card": "number_card"}
_SKIP = {"node_modules", "public", "templates", "www", "tests", "test", "__pycache__", "locale", "patches", "fixtures", "docs", "config", "translations"}
_STR = r'"((?:[^"\\]|\\.)*)"'


def _kv(text: str, key: str) -> str | None:
	m = re.search(r'^ "%s": %s' % (key, _STR), text, re.M)
	if not m:
		return None
	try:
		return json.loads('"' + m.group(1) + '"')
	except ValueError:
		return m.group(1)


def _flag(text: str, key: str) -> int:
	m = re.search(r'^ "%s": (\d+)' % key, text, re.M)
	return int(m.group(1)) if m else 0


# ---- scanning ----------------------------------------------------------------

def _git_head(repo: Path) -> str:
	"""HEAD commit read straight from .git (no subprocess); '' when unavailable."""
	try:
		g = repo / ".git"
		if g.is_file():
			rel = g.read_text().strip().split("gitdir:", 1)[1].strip()
			g = (repo / rel).resolve()
		head = (g / "HEAD").read_text().strip()
		if head.startswith("ref:"):
			ref = head.split(None, 1)[1]
			f = g / ref
			if f.is_file():
				return f.read_text().strip()
			common = g / "commondir"
			base = (g / common.read_text().strip()).resolve() if common.is_file() else g
			for d in (base / ref, ):
				if d.is_file():
					return d.read_text().strip()
			for line in (base / "packed-refs").read_text().splitlines():
				if line.endswith(" " + ref):
					return line.split()[0]
			return ref
		return head
	except (OSError, IndexError):
		return ""


def _scan_kind_dir(kdir: Path, kind: str, out: list):
	"""Append (kind, path, mtime_ns, size) for <kdir>/<e>/<e>.json and flat <kdir>/*.json."""
	try:
		it = list(os.scandir(kdir))
	except OSError:
		return
	for e in it:
		try:
			if e.is_dir():
				f = os.path.join(e.path, e.name + ".json")
				st = os.stat(f)
			elif e.name.endswith(".json") and kind == "sidebar":
				f, st = e.path, e.stat()
			else:
				continue
		except OSError:
			continue
		out.append((kind, f, st.st_mtime_ns, st.st_size))


def _scan_package(pkg: Path) -> list:
	out: list = []
	try:
		tops = list(os.scandir(pkg))
	except OSError:
		return out
	for t in tops:
		if not t.is_dir() or t.name.startswith((".", "__")) or t.name in _SKIP:
			continue
		if t.name in KIND_DIRS:  # app-rooted (sidebar/, workspace_sidebar/, ...)
			_scan_kind_dir(Path(t.path), KIND_DIRS[t.name], out)
			continue
		try:
			subs = list(os.scandir(t.path))
		except OSError:
			continue
		for s in subs:
			if s.name in KIND_DIRS and s.is_dir():
				_scan_kind_dir(Path(s.path), KIND_DIRS[s.name], out)
	fx = pkg / "fixtures" / "custom_html_block.json"
	try:
		st = fx.stat()
		out.append(("fixture:custom_block", str(fx), st.st_mtime_ns, st.st_size))
	except OSError:
		pass
	return out


def _apps_to_scan(app: App) -> tuple[list[tuple[str, Path]], Path | None]:
	from .icons import find_bench, installed_packages
	bench = find_bench(app)
	return installed_packages(app, bench), bench


def _fingerprint(scans: dict, heads: dict) -> str:
	h = hashlib.sha256()
	h.update(f"v{SCHEMA}".encode())
	for name in sorted(scans):
		h.update(f"\0A{name}\0{heads.get(name, '')}".encode())
		for kind, f, mt, sz in sorted(scans[name], key=lambda r: r[1]):
			h.update(f"\0{kind}\0{f}\0{mt}\0{sz}".encode())
	return h.hexdigest()


def _gather(app: App) -> tuple[dict, dict, Path | None]:
	pkgs, bench = _apps_to_scan(app)
	scans, heads = {}, {}
	for name, pkg in pkgs:
		scans[name] = _scan_package(pkg)
		heads[name] = _git_head(pkg.parent)
	return scans, heads, bench


def _read_entity(kind: str, f: str) -> tuple[str, dict] | None:
	try:
		text = Path(f).read_text(encoding="utf-8", errors="replace")
	except OSError:
		return None
	name = _kv(text, "name")
	if name is None:
		try:
			d = json.loads(text)
			name = d.get("name") or d.get("title") or d.get("label")
		except ValueError:
			name = None
		if not name:
			name = Path(f).stem
	meta: dict = {}
	if kind == "doctype":
		meta = {"module": _kv(text, "module"), "issingle": _flag(text, "issingle"), "istable": _flag(text, "istable")}
	elif kind == "report":
		meta = {"ref_doctype": _kv(text, "ref_doctype"), "module": _kv(text, "module")}
		name = _kv(text, "report_name") or name
	elif kind == "page":
		name = _kv(text, "page_name") and name or name
		meta = {"module": _kv(text, "module")}
	else:
		meta = {"module": _kv(text, "module")}
	return name, {k: v for k, v in meta.items() if v not in (None, "")}


def _build(scans: dict, heads: dict, bench: Path | None, fingerprint: str) -> dict:
	items: dict[str, dict] = {k: {} for k in KINDS}
	for app_name, rows in scans.items():
		for kind, f, _, _ in rows:
			if kind == "fixture:custom_block":
				try:
					data = json.loads(Path(f).read_text(encoding="utf-8"))
					for d in data if isinstance(data, list) else []:
						if isinstance(d, dict) and d.get("name"):
							items["custom_block"].setdefault(d["name"], {"app": app_name})
				except (OSError, ValueError):
					pass
				continue
			ent = _read_entity(kind, f)
			if ent:
				name, meta = ent
				items[kind].setdefault(name, {"app": app_name, **meta})
	return {
		"schema": SCHEMA,
		"fingerprint": fingerprint,
		"bench": str(bench) if bench else None,
		"apps": {n: heads.get(n, "") for n in sorted(scans)},
		"items": items,
	}


# ---- cache io ------------------------------------------------------------------

def cache_path(app: App) -> Path:
	"""Outside the app repo by default: $FDESK_CACHE_DIR, else $XDG_CACHE_HOME/~/.cache + fdesk/<hash of app path>/."""
	import hashlib
	import os

	base = os.environ.get("FDESK_CACHE_DIR")
	if not base:
		base = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "fdesk")
	key = hashlib.sha1(str(Path(app.root).resolve()).encode()).hexdigest()[:12]
	return Path(base) / key / CACHE_FILE


def _valid(cat) -> bool:
	return (
		isinstance(cat, dict) and cat.get("schema") == SCHEMA and isinstance(cat.get("fingerprint"), str)
		and isinstance(cat.get("items"), dict) and all(isinstance(cat["items"].get(k), dict) for k in KINDS)
		and isinstance(cat.get("apps"), dict)
	)


def _read_cache(path: Path):
	try:
		cat = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, ValueError, UnicodeDecodeError, RecursionError):
		return None
	return cat if _valid(cat) else None


def _write_cache(path: Path, cat: dict) -> bool:
	tmp = None
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".catalog-", suffix=".tmp")
		with os.fdopen(fd, "w", encoding="utf-8") as fh:
			json.dump(cat, fh, separators=(",", ":"), sort_keys=True)
			fh.flush()
			os.fsync(fh.fileno())
		os.replace(tmp, path)
		return True
	except OSError:
		if tmp:
			try:
				os.unlink(tmp)
			except OSError:
				pass
		return False


def _ensure(app: App, force: bool = False) -> tuple[dict, bool, bool]:
	"""(catalog, rebuilt, persisted)."""
	scans, heads, bench = _gather(app)
	fp = _fingerprint(scans, heads)
	path = cache_path(app)
	if not force:
		cat = _read_cache(path)
		if cat and cat["fingerprint"] == fp:
			return cat, False, True
	cat = _build(scans, heads, bench, fp)
	return cat, True, _write_cache(path, cat)


def _counts(cat: dict) -> dict:
	return {k: len(cat["items"].get(k, {})) for k in KINDS}


# ---- public API -----------------------------------------------------------------

def refresh(app: App | None = None, force: bool = False) -> dict:
	"""Rebuild if fingerprint changed or cache unreadable; return {rebuilt: bool, fingerprint, counts}."""
	app = app or App.find()
	cat, rebuilt, persisted = _ensure(app, force)
	return {"rebuilt": rebuilt, "fingerprint": cat["fingerprint"], "counts": _counts(cat), "bench": cat["bench"], "apps": list(cat["apps"]), "cache": str(cache_path(app)), "persisted": persisted}


def lookup(kind: str, name: str, app: App | None = None) -> bool | None:
	"""True if `name` exists for kind (doctype|report|page|workspace|dashboard|chart|number_card|custom_block|sidebar),
	False if the catalog is healthy and it does not, None if the catalog cannot answer (no bench / frappe checkout)."""
	kind = ALIASES.get(kind, kind)
	if kind not in KINDS:
		raise FdeskError(f"unknown catalog kind {kind!r}; expected one of {KINDS}")
	try:
		app = app or App.find()
		cat, _, _ = _ensure(app)
	except (FdeskError, OSError):
		return None
	if not cat.get("bench") or "frappe" not in cat["apps"]:
		return None
	return name in cat["items"][kind]


# ---- CLI --------------------------------------------------------------------------

def _emit(args, text: str) -> None:
	if not getattr(args, "json", False):
		print(text)


def cmd_refresh(args) -> Result:
	res = Result()
	info = refresh(App.find(args.app), force=args.force)
	res.info.update(info)
	_emit(args, f"{'rebuilt' if info['rebuilt'] else 'up to date'}: " + ", ".join(f"{k}={v}" for k, v in info["counts"].items()))
	if not info["bench"]:
		res.warnings.append("no bench found (set FDESK_BENCH); catalog covers this app only and lookups will answer unknown")
	return res


def cmd_status(args) -> Result:
	res = Result()
	app = App.find(args.app)
	scans, heads, bench = _gather(app)
	fp = _fingerprint(scans, heads)
	path = cache_path(app)
	cat = _read_cache(path)
	state = "missing" if not path.exists() else "corrupt" if cat is None else "fresh" if cat["fingerprint"] == fp else "stale"
	res.info.update({"cache": str(path), "state": state, "fingerprint": fp, "bench": str(bench) if bench else None, "apps": sorted(scans), "counts": _counts(cat) if cat else None})
	_emit(args, f"{state}: {path}\nbench: {bench}\napps: {', '.join(sorted(scans))}" + (("\n" + ", ".join(f"{k}={v}" for k, v in _counts(cat).items())) if cat else ""))
	return res


def cmd_search(args) -> Result:
	res = Result()
	app = App.find(args.app)
	cat, _, _ = _ensure(app)
	q = args.query.lower()
	kinds = [ALIASES.get(args.kind, args.kind)] if args.kind else KINDS
	for k in kinds:
		if k not in KINDS:
			raise FdeskError(f"unknown catalog kind {k!r}; expected one of {KINDS}")
	hits = []
	for k in kinds:
		for n, meta in cat["items"][k].items():
			nl = n.lower()
			if q in nl:
				hits.append((0 if nl == q else 1 if nl.startswith(q) else 2, len(n), k, n, meta))
	hits.sort(key=lambda t: t[:4])
	hits = hits[: max(args.limit, 0)]
	res.info["results"] = [{"kind": k, "name": n, **m} for _, _, k, n, m in hits]
	_emit(args, "\n".join(f"{k}\t{n}\t{m.get('app', '')}" for _, _, k, n, m in hits))
	return res


def register(sub):
	note = "The cache (~/.cache/fdesk/..., or $FDESK_CACHE_DIR) is ALWAYS written: --write/dry-run semantics do not apply to it."
	g = sub.add_parser("catalog", help="self-healing cache of linkable entities (always written; --write does not apply)", description=note)
	gs = g.add_subparsers(dest="cmd", required=True)
	p = gs.add_parser("refresh", help="rebuild the cache if stale (always written to the user cache dir)")
	p.add_argument("--force", action="store_true", help="rebuild even when the fingerprint matches")
	p.set_defaults(func=cmd_refresh)
	p = gs.add_parser("status", help="show cache state (fresh|stale|missing|corrupt) without writing")
	p.set_defaults(func=cmd_status)
	p = gs.add_parser("search", help="substring search over cataloged names")
	p.add_argument("query")
	p.add_argument("--kind", default=None)
	p.add_argument("--limit", type=int, default=20)
	p.set_defaults(func=cmd_search)
