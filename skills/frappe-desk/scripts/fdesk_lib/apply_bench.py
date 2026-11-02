"""apply / verify / snapshot / restore against a bench (host or docker). Never runs git.

Facts: frappe imports a standard Sidebar/Dock only when file `modified` > DB `modified`
(frappe/modules/import_file.py); Custom Sidebar fields: frappe/desk/doctype/custom_sidebar/custom_sidebar.json;
dock_item rows use `title`; `bench execute` / `bench console` are the batching vehicles.
"""

from __future__ import annotations

import hashlib
import json
import re
import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

from .common import App, FdeskError, Result, load

NAV_DIRS = ("sidebar", "dock", "workspace", "number_card", "dashboard_chart")
DOCTYPES = {"sidebar": "Sidebar", "dock": "Dock", "workspace": "Workspace",
	"number_card": "Number Card", "dashboard_chart": "Dashboard Chart"}
MARK = "FDESK_JSON:"
SNAP_DIR = ".fdesk-snapshots"


# ---------------------------------------------------------------- runner

def run(cmd: list[str]) -> tuple[int, str]:
	"""Single subprocess choke point; tests monkeypatch this. cmd is an argv list."""
	p = subprocess.run(cmd, capture_output=True, text=True)
	return p.returncode, (p.stdout or "") + (p.stderr or "")


def _wrap(args, shell_cmd: str) -> list[str]:
	"""Local: bash -c in the bench dir. Docker etc: runner template with the shell string appended."""
	cwd = getattr(args, "bench_cwd", None) or args.bench
	inner = f"cd {shlex.quote(str(cwd))} && {shell_cmd}"
	if getattr(args, "runner", None):
		return shlex.split(args.runner) + [inner]
	return ["bash", "-c", inner]


def _show(argv: list[str]) -> str:
	return " ".join(shlex.quote(a) if i < len(argv) - 1 else repr(a) if "\n" in a else shlex.quote(a) for i, a in enumerate(argv))


def _console(args, script: str) -> str:
	return f"bench --site {shlex.quote(args.site)} console <<'FDESK_EOF'\n{script}\nFDESK_EOF"


# ---------------------------------------------------------------- app files

def _frappe_version(bench) -> str:
	p = Path(bench) / "apps" / "frappe" / "frappe" / "__init__.py"
	if not p.exists():
		raise FdeskError(f"cannot read frappe version: {p} missing")
	m = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", p.read_text())
	if not m:
		raise FdeskError(f"no __version__ in {p}")
	return m.group(1)


def _require_v16(bench) -> str:
	v = _frappe_version(bench)
	if v.split(".")[0] != "16":
		raise FdeskError(f"frappe {v} found; fdesk apply/verify only supports v16 (Sidebar/Dock doctypes)")
	return v


def nav_files(app: App) -> list[Path]:
	out = []
	for p in sorted(app.package.rglob("*.json")):
		rel = p.relative_to(app.package).parts
		if len(rel) >= 3 and rel[-3] in NAV_DIRS:
			out.append(p)
	fx = app.package / "fixtures" / "custom_html_block.json"
	if fx.exists():
		out.append(fx)
	return out


def entities(app: App) -> list[dict]:
	"""[{doctype, name, modified, doc, path}] from files."""
	ents = []
	for p in nav_files(app):
		d = load(p)
		if isinstance(d, list):
			for r in d:
				ents.append({"doctype": "Custom HTML Block", "name": r.get("name"), "modified": r.get("modified"), "doc": r, "path": p})
		else:
			ents.append({"doctype": d.get("doctype") or DOCTYPES.get(p.parts[-3]), "name": d.get("name"),
				"modified": d.get("modified"), "doc": d, "path": p})
	return ents


def _norm_ts(s):
	if not s:
		return None
	s = str(s)
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
		try:
			return datetime.strptime(s, fmt)
		except ValueError:
			pass
	return None


def summarize(doc: dict) -> dict:
	"""Comparable shape of a nav document (same function for file and DB side)."""
	s = {}
	items = doc.get("items") or []
	if doc.get("doctype") == "Dock" or "app" in doc and "items" in doc and not any("label" in i for i in items):
		s["tiles"] = [i.get("title") for i in items]
	else:
		s["items"] = [[i.get("type"), i.get("label"), i.get("icon")] for i in items]
	if doc.get("shortcuts") is not None:
		s["shortcuts"] = [x.get("label") for x in doc.get("shortcuts") or []]
	if doc.get("content"):
		try:
			s["blocks"] = len(json.loads(doc["content"]))
		except (ValueError, TypeError):
			s["blocks"] = -1
	return s


def _probe_script(ents: list[dict]) -> str:
	spec = json.dumps([[e["doctype"], e["name"]] for e in ents])
	return (
		"import frappe, json\n"
		f"spec = json.loads({spec!r})\n"
		"out = {'docs': {}, 'overrides': [], 'developer_mode': bool(frappe.conf.developer_mode)}\n"
		"for dt, n in spec:\n"
		"    if frappe.db.exists(dt, n):\n"
		"        out['docs'][dt + '|' + n] = frappe.get_doc(dt, n).as_dict(no_nulls=True)\n"
		"    else:\n"
		"        out['docs'][dt + '|' + n] = None\n"
		"if frappe.db.exists('DocType', 'Custom Sidebar'):\n"
		"    out['overrides'] = frappe.get_all('Custom Sidebar', fields=['name', 'module', 'user', 'label'])\n"
		f"print({MARK!r} + json.dumps(out, default=str))"
	)


def _probe(args, ents) -> dict:
	rc, out = run(_wrap(args, _console(args, _probe_script(ents))))
	for line in out.splitlines():
		i = line.find(MARK)
		if i >= 0:
			return json.loads(line[i + len(MARK):])
	raise FdeskError(f"probe failed (rc={rc}); no {MARK} marker in output:\n{out[-400:]}")


def _changed(ents, probe) -> list[dict]:
	ch = []
	for e in ents:
		db = probe["docs"].get(f"{e['doctype']}|{e['name']}")
		if db is None:
			ch.append(e)
			continue
		fm, dm = _norm_ts(e["modified"]), _norm_ts(db.get("modified"))
		if fm is None or dm is None or fm > dm:
			ch.append(e)
			continue
		if e["doctype"] != "Custom HTML Block":
			# same `modified` but the DB rows differ from the file (hand-edited site, interrupted import): frappe would skip it
			fs, ds = summarize(e["doc"]), summarize(db)
			if any(fs[k] != ds.get(k) for k in fs):
				ch.append(e)
	return ch


# ---------------------------------------------------------------- apply

def _container_path(args, host_path) -> str:
	"""Host path under --bench -> the same file as the runner sees it (--bench-cwd)."""
	rel = os.path.relpath(str(host_path), str(args.bench))
	return os.path.join(args.bench_cwd or str(args.bench), rel)


def cmd_apply(args) -> Result:
	app = App.find(args.app)
	res = Result()
	_require_v16(args.bench)
	ents = [e for e in entities(app) if e["doctype"] in ("Sidebar", "Dock", "Workspace", "Number Card", "Dashboard Chart", "Custom HTML Block")]
	site = shlex.quote(args.site)
	steps: list[tuple[str, list[str]]] = []
	probe_cmd = _wrap(args, _console(args, _probe_script(ents)))
	steps.append(("probe", probe_cmd))
	reset_targets = [e for e in ents if e["doctype"] in ("Sidebar", "Dock", "Workspace")]
	if args.reset_nav:
		steps.append(("reset-nav", None))  # built at run time from changed set
	steps.append(("migrate", _wrap(args, f"bench --site {site} migrate")))
	if args.reset_nav:
		steps.append(("reimport-dock", None))  # built at run time (frappe imports Dock before Sidebar, which empties it)
	steps.append(("clear-cache", _wrap(args, f"bench --site {site} clear-cache")))
	steps.append(("touch-hooks", _wrap(args, f"touch apps/{app.name}/{app.name}/hooks.py")))
	if args.restart_cmd:
		steps.append(("restart", _wrap(args, args.restart_cmd)))
	res.info["workers_note"] = "restart queue/scheduler/socketio workers if they cache app code (pass --restart-cmd to automate)"

	def show_step(name, argv):
		if argv is None and name == "reimport-dock":
			return "reimport-dock: re-import changed Dock files after migrate (only when a Dock was reset)"
		if argv is None:
			names = [e["name"] for e in reset_targets]
			return f"reset-nav: delete changed Sidebar/Dock/Workspace rows (candidates: {names}; filtered to changed at --write)"
		return f"{name}: {_show(argv)}"

	res.info["commands"] = [show_step(n, a) for n, a in steps]
	if not args.write:
		if not args.json:
			for c in res.info["commands"]:
				print(c)
		res.info["dry_run"] = True
		return res

	ran = []
	for name, argv in steps:
		if name == "probe":
			probe = _probe(args, ents)
			changed = _changed(ents, probe)
			res.info["changed"] = [f"{e['doctype']}|{e['name']}" for e in changed]
			if not probe.get("developer_mode"):
				res.warnings.append("developer_mode is off: standard=1 docs cannot be edited/exported from the desk; migrate still imports from files")
			ran.append({"step": "probe", "rc": 0})
			continue
		if name == "reimport-dock":
			docks = [e for e in changed if e["doctype"] == "Dock"]
			if not docks:
				continue
			# frappe imports Dock before Sidebar on migrate; a Dock row pointing at a not-yet-imported Sidebar loses its tiles.
			paths = [_container_path(args, e["path"]) for e in docks]
			script = "import frappe\nfrom frappe.modules.import_file import import_file_by_path\n" + "".join(
				f"import_file_by_path({pp!r}, force=True)\n" for pp in paths) + "frappe.db.commit()"
			argv = _wrap(args, _console(args, script))
		if name == "reset-nav":
			victims = [e for e in changed if e["doctype"] in ("Sidebar", "Dock", "Workspace")]
			if not victims:
				continue
			# Never frappe.delete_doc here: in developer mode it deletes the app's JSON file from disk
			# and Workspace.on_trash rewrites Custom Sidebar rows. Remove only the DB rows; migrate re-imports from the files.
			script = (
				"import frappe\n"
				"def _drop(dt, n):\n"
				"    for tf in frappe.get_meta(dt).get_table_fields():\n"
				"        frappe.db.delete(tf.options, {'parent': n, 'parenttype': dt})\n"
				"    frappe.db.delete(dt, {'name': n})\n"
				+ "".join(f"_drop({e['doctype']!r}, {e['name']!r})\n" for e in victims)
				+ "frappe.db.commit()"
			)
			argv = _wrap(args, _console(args, script))
		rc, out = run(argv)
		ran.append({"step": name, "rc": rc})
		if rc != 0:
			res.ok = False
			res.errors.append(f"{name} failed (rc={rc}): {out[-300:]}")
			break
	res.info["ran"] = ran
	return res


# ---------------------------------------------------------------- verify

def cmd_verify(args) -> Result:
	app = App.find(args.app)
	res = Result()
	_require_v16(args.bench)
	ents = entities(app)
	probe = _probe(args, ents)
	mism = []
	for e in ents:
		key = f"{e['doctype']}|{e['name']}"
		db = probe["docs"].get(key)
		if db is None:
			mism.append({"entity": key, "problem": "missing in DB"})
			continue
		fm, dm = _norm_ts(e["modified"]), _norm_ts(db.get("modified"))
		if e["doctype"] == "Custom HTML Block":
			# fixtures are overwritten on every migrate and records need not carry `modified`: compare content
			for f in ("html", "script", "style"):
				if (e["doc"].get(f) or "") != (db.get(f) or ""):
					mism.append({"entity": key, "problem": f"{f} differs"})
		elif fm != dm:
			mism.append({"entity": key, "problem": "modified differs", "file": e["modified"], "db": db.get("modified")})
		fs, ds = summarize(e["doc"]), summarize(db)
		for k in fs:
			if fs[k] != ds.get(k):
				mism.append({"entity": key, "problem": f"{k} differs", "file": fs[k], "db": ds.get(k)})
	overrides = probe.get("overrides", [])
	dev = probe.get("developer_mode")
	res.info.update({"ok": not mism, "mismatches": mism, "overrides": overrides, "developer_mode": dev})
	res.ok = not mism
	if overrides:
		res.warnings.append(f"L16 info: {len(overrides)} Custom Sidebar row(s) can override the app sidebar")
	if not dev:
		res.warnings.append("developer_mode is off (needed to edit standard=1 docs)")
	if mism:
		res.errors.append(f"{len(mism)} mismatch(es) between files and DB")
	return res


# ---------------------------------------------------------------- snapshot / restore

def _sha(b: bytes) -> str:
	return hashlib.sha256(b).hexdigest()


def _rel(app, p):
	return str(Path(p).relative_to(app.root))


def cmd_snapshot(args) -> Result:
	app = App.find(args.app)
	res = Result()
	name = args.name or datetime.now().strftime("%Y%m%d-%H%M%S")
	if not re.fullmatch(r"[\w.\-]+", name):
		raise FdeskError("snapshot name may only contain letters, digits, _ . -")
	base = app.root / SNAP_DIR / name
	manifest = {}
	for p in nav_files(app):
		b = p.read_bytes()
		manifest[_rel(app, p)] = _sha(b)
		_plan_text(res, base / "files" / _rel(app, p), b.decode("utf-8"))
	_plan_text(res, base / "manifest.json", json.dumps({"files": manifest}, indent=1, sort_keys=True) + "\n")
	res.info.update({"snapshot": str(base), "files": len(manifest)})
	return res


def _plan_text(res, path: Path, text: str):
	before = path.read_text(encoding="utf-8") if path.exists() else None
	if before == text:
		return
	res.changes.append({"path": str(path), "action": "create" if before is None else "update", "diff": "", "_text": text})


def cmd_restore(args) -> Result:
	app = App.find(args.app)
	res = Result()
	base = app.root / SNAP_DIR / args.name
	mf = base / "manifest.json"
	if not mf.exists():
		raise FdeskError(f"no snapshot {args.name!r} at {base}")
	files = json.loads(mf.read_text())["files"]
	for rel, sha in files.items():
		src = base / "files" / rel
		if not src.exists() or _sha(src.read_bytes()) != sha:
			raise FdeskError(f"snapshot {args.name!r} is corrupt at {rel}")
		_plan_text(res, app.root / rel, src.read_text(encoding="utf-8"))
	extra = [_rel(app, p) for p in nav_files(app) if _rel(app, p) not in files]
	for rel in extra:
		if args.prune:
			res.changes.append({"path": str(app.root / rel), "action": "delete", "diff": ""})
		else:
			res.warnings.append(f"created after snapshot (kept; use --prune to delete): {rel}")
	res.info["differences"] = [f"{c['action']}: {c['path']}" for c in res.changes]
	return res


# ---------------------------------------------------------------- registration

def register(sub):
	def bench_args(p, restart=False):
		p.add_argument("--bench", required=True, help="bench dir on this host (used to read frappe version)")
		p.add_argument("--bench-cwd", default=None, help="bench dir as seen by the runner (default: --bench)")
		p.add_argument("--site", required=True)
		p.add_argument("--runner", default=None, help='command template, e.g. "docker exec CONTAINER bash -c"')

	g = sub.add_parser("apply", help="migrate + clear-cache + reload on a bench")
	g.set_defaults(func=cmd_apply)
	bench_args(g)
	g.add_argument("--reset-nav", action="store_true", help="delete changed Sidebar/Dock rows before migrate")
	g.add_argument("--restart-cmd", default=None)

	g = sub.add_parser("verify", help="compare DB vs files")
	g.set_defaults(func=cmd_verify)
	bench_args(g)

	g = sub.add_parser("snapshot", help="copy nav JSON files to .fdesk-snapshots")
	g.add_argument("--name", default=None)
	g.set_defaults(func=cmd_snapshot)

	g = sub.add_parser("restore", help="restore nav JSON files from a snapshot")
	g.add_argument("--name", required=True)
	g.add_argument("--prune", action="store_true")
	g.set_defaults(func=cmd_restore)
