"""Custom HTML Block commands: `block new|list|remove|render-preview`, plus `lint_block(record)` for the lint module.

Facts (verified in frappe source):
- Custom HTML Block fields are only html, script, style, roles, private; autoname is Prompt, no module/standard,
  so blocks ship as a fixture: frappe/desk/doctype/custom_html_block/custom_html_block.json
- fixtures are read from frappe.get_app_path(app, "fixtures", *.json) and imported with import_doc (always overwrites):
  frappe/utils/fixtures.py (import_fixtures, export_fixtures)
- rendering: frappe.create_shadow_element (frappe/public/js/frappe/dom.js) wraps the script in an IIFE that defines
  `root_element` (the shadow root); html goes through remove_script_and_style; CSS custom properties inherit into the shadow.
- workspace wiring: frappe/public/js/frappe/widgets/custom_block_widget.js (content block data.custom_block_name,
  child table custom_blocks {custom_block_name, label}).
"""

from __future__ import annotations

import argparse
import ast
import html as htmllib
import json
import re
import sys
from pathlib import Path

from . import workspace
from .common import (
	App, FdeskError, Result, detect_style, load, plan_write, render, scrub, unified_diff,
)

DT = "Custom HTML Block"
KINDS = ["stat", "kpi-row", "list", "progress", "empty", "links"]
TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "blocks"
FIXTURE_STYLE = {"indent": 1, "sort_keys": True, "ensure_ascii": True, "newline": True}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DOCTYPE_RE = re.compile(r"^[\w][\w \-/&().,]*$")
ORDER_RE = re.compile(r"^[A-Za-z_][\w]*( (asc|desc))?(, ?[A-Za-z_][\w]*( (asc|desc))?)*$", re.I)

# ---------------------------------------------------------------- template parameters
_DEFAULT_ITEMS = [
	{"label": "Open ToDos", "doctype": "ToDo", "filters": {"status": "Open"}, "route": "List/ToDo"},
	{"label": "All ToDos", "doctype": "ToDo"},
]
# kind -> {param: (default, type)}; type in str|int|json|ident|doctype|aggregate|route|order
PARAMS = {
	"stat": {
		"doctype": ("ToDo", "doctype"), "filters": ({}, "json"), "field": ("", "ident"),
		"aggregate": ("count", "aggregate"), "route": (None, "route"), "prefix": ("", "str"), "suffix": ("", "str"),
		"hide_zero": (False, "bool"), "empty_text": ("No data yet.", "str"),
	},
	"kpi-row": {"items": (_DEFAULT_ITEMS, "items"), "empty_text": ("No KPIs configured.", "str")},
	"list": {
		"doctype": ("ToDo", "doctype"), "filters": ({}, "json"), "title_field": ("name", "ident"),
		"subtitle_field": ("modified", "ident"), "limit": (5, "int"), "order_by": ("modified desc", "order"),
		"empty_text": ("Nothing to show.", "str"),
	},
	"progress": {
		"doctype": ("ToDo", "doctype"), "filters": ({"status": "Closed"}, "json"), "total_filters": ({}, "json"),
		"label": ("closed", "str"), "empty_text": ("Nothing to measure yet.", "str"),
	},
	"empty": {"message": ("Nothing here yet.", "str"), "action_label": ("", "str"), "action_route": ("", "route")},
	"links": {
		"items": ([{"label": "ToDo list", "route": "List/ToDo", "description": "Open your tasks"}], "links"),
		"empty_text": ("No links configured.", "str"),
	},
}
DEFAULT_TITLES = {"stat": "Total", "kpi-row": "Key figures", "list": "Recent", "progress": "Progress", "empty": "Nothing here yet", "links": "Quick links"}


def _coerce(kind: str, key: str, raw, typ: str):
	def bad(msg):
		raise FdeskError(f"--param {key}: {msg}")

	if typ in ("json", "items", "links"):
		val = json.loads(raw) if isinstance(raw, str) else raw
		if typ == "json" and not isinstance(val, (dict, list)):
			bad("expected a JSON object or list (frappe filters)")
		if typ in ("items", "links"):
			if not isinstance(val, list) or not all(isinstance(i, dict) for i in val):
				bad("expected a JSON list of objects")
			for i in val:
				if typ == "links" and not (isinstance(i.get("label"), str) and isinstance(i.get("route"), str)):
					bad("each link needs string label and route")
				if typ == "items":
					if not (isinstance(i.get("label"), str) and DOCTYPE_RE.match(str(i.get("doctype", "")))):
						bad("each item needs a label and a doctype")
					if i.get("field") and not IDENT.match(str(i["field"])):
						bad(f"invalid field {i['field']!r}")
					if i.get("aggregate", "count") not in ("count", "sum", "avg"):
						bad("aggregate must be count|sum|avg")
		return val
	if typ == "bool":
		return str(raw).lower() in ("1", "true", "yes") if not isinstance(raw, bool) else raw
	if typ == "int":
		try:
			n = int(raw)
		except (TypeError, ValueError):
			bad("expected an integer")
		if not 1 <= n <= 50:
			bad("expected 1..50")
		return n
	raw = "" if raw is None else str(raw)
	if typ == "doctype" and not DOCTYPE_RE.match(raw):
		bad(f"invalid doctype {raw!r}")
	if typ == "ident" and raw and not IDENT.match(raw):
		bad(f"invalid field name {raw!r}")
	if typ == "aggregate" and raw not in ("count", "sum", "avg"):
		bad("expected count|sum|avg")
	if typ == "order" and not ORDER_RE.match(raw):
		bad(f"invalid order_by {raw!r}")
	if typ == "route" and raw and not re.match(r"^[\w][\w \-/&().,]*$", raw):
		bad(f"invalid route {raw!r} (e.g. List/Sales Invoice)")
	return raw


def build_config(kind: str, overrides: dict) -> dict:
	spec = PARAMS[kind]
	unknown = sorted(set(overrides) - set(spec))
	if unknown:
		raise FdeskError(f"unknown --param for kind {kind}: {', '.join(unknown)}; valid: {', '.join(sorted(spec))}")
	cfg = {}
	for key, (default, typ) in spec.items():
		cfg[key] = _coerce(kind, key, overrides[key], typ) if key in overrides else default
	if kind == "stat":
		if cfg["aggregate"] != "count" and not cfg["field"]:
			raise FdeskError("--param aggregate=sum|avg needs --param field=<numeric field>")
		if cfg["route"] is None:
			cfg["route"] = "List/" + cfg["doctype"]
	return cfg


def _read(name: str) -> str:
	p = TEMPLATES / name
	if not p.exists():
		raise FdeskError(f"template file missing: {p}")
	return p.read_text(encoding="utf-8")


def _js_json(value) -> str:
	return json.dumps(value, ensure_ascii=True, sort_keys=True).replace("</", "<\\/")


def render_template(kind: str, title: str, overrides: dict | None = None) -> dict:
	"""Deterministic html/style/script triple for a template kind."""
	if kind not in KINDS:
		raise FdeskError(f"unknown kind {kind!r}; expected one of {KINDS}")
	cfg = build_config(kind, overrides or {})
	out_html = _read(f"{kind}.html").replace("{{title}}", htmllib.escape(title, quote=True))
	style = _read("_common.css").rstrip("\n") + "\n" + _read(f"{kind}.css")
	script = _read("_common.js").replace("{{config}}", _js_json(cfg)).rstrip("\n") + "\n" + _read(f"{kind}.js")
	for label, text in (("html", out_html), ("style", style), ("script", script)):
		if "{{" in text:
			raise FdeskError(f"unresolved placeholder in {kind} {label}")
	return {"html": out_html, "style": style, "script": script}


# ---------------------------------------------------------------- lint
_EXT_URL = re.compile(r"https?://|@import\b|[\"'(=]\s*//[\w-]+\.", re.I)
_VAR = re.compile(r"var\([^()]*(?:\([^()]*\)[^()]*)*\)")
_HEX_DECL = re.compile(r":[^;{}]*#[0-9a-fA-F]{3,8}\b")
_COLOR_FN = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\s*\(", re.I)
_NAMED = re.compile(r"(?:color|background(?:-color)?|border(?:-[a-z]+)?|outline(?:-color)?|fill|stroke)\s*:[^;{}]*\b(?:black|white|red|green|blue|gray|grey|yellow|orange|purple|pink)\b", re.I)
_DOM_USE = re.compile(r"\b(?:querySelector(?:All)?|getElementById|getElementsBy\w+|createElement|innerHTML|appendChild|addEventListener|classList)\b|\.style\b")
_BAD_DOC = re.compile(r"\bdocument\s*\.\s*(?:querySelector(?:All)?|getElementById|getElementsBy\w+|body|documentElement)\b|\bwindow\.document\b")


def _decl_text(record: dict) -> list[tuple[str, str]]:
	parts = [("style", record.get("style") or "")]
	for m in re.finditer(r"style\s*=\s*(\"[^\"]*\"|'[^']*')", record.get("html") or "", re.I):
		parts.append(("html style attribute", m.group(1)[1:-1]))
	return parts


def lint_block(record: dict) -> list[str]:
	"""Return a list of problems (empty when the block is fine). Rules map to lint L12."""
	issues: list[str] = []
	name = record.get("name") or ""
	if record.get("doctype") not in (None, DT):
		issues.append(f"doctype must be {DT!r}")
	if not str(name).strip():
		issues.append("name is empty")
	html, script, style = (record.get(k) or "" for k in ("html", "script", "style"))
	for label, text in (("html", html), ("script", script), ("style", style)):
		if _EXT_URL.search(text):
			issues.append(f"{label}: external URL or @import (blocks must make no external requests)")
	if re.search(r"<\s*(script|style|link)\b", html, re.I):
		issues.append("html: <script>/<style>/<link> are stripped by Frappe; use the script and style fields")
	for where, text in _decl_text(record):
		bare = _VAR.sub("", text)
		if _HEX_DECL.search(bare):
			issues.append(f"{where}: hard-coded hex color outside var(--token, #fallback); use a CSS variable")
		if _COLOR_FN.search(bare):
			issues.append(f"{where}: hard-coded rgb()/hsl() color outside var(); use a CSS variable")
		if _NAMED.search(bare):
			issues.append(f"{where}: named color; use a CSS variable")
	if style.strip() and "var(--" not in style:
		issues.append("style: uses no var(--token) at all; blocks should inherit desk CSS variables")
	if _BAD_DOC.search(script):
		issues.append("script: document.querySelector/getElementById/body is wrong inside the shadow root; use root_element")
	if _DOM_USE.search(script) and "root_element" not in script:
		issues.append("script: touches the DOM but never uses root_element")
	if str(record.get("private", 0)) not in ("0", "False"):
		issues.append("private must be 0 (a private block is only visible to its owner)")
	roles = record.get("roles", [])
	if not isinstance(roles, list):
		issues.append("roles must be a list of {role: Name}")
	else:
		seen = set()
		for r in roles:
			role = r.get("role") if isinstance(r, dict) else None
			if not isinstance(role, str) or not role.strip():
				issues.append(f"roles: invalid entry {r!r}; expected {{'role': 'Role Name'}}")
			elif role in seen:
				issues.append(f"roles: duplicate role {role!r}")
			else:
				seen.add(role)
	return issues


# ---------------------------------------------------------------- file helpers
def _fixture_path(app: App) -> Path:
	return app.package / "fixtures" / "custom_html_block.json"


def _plan_text(res: Result, path: Path, after: str, action: str | None = None) -> None:
	before = path.read_text(encoding="utf-8") if path.exists() else ""
	if action == "delete":
		if path.exists():
			res.changes.append({"path": str(path), "action": "delete", "diff": unified_diff(before, "", path.name)})
		return
	if before == after:
		return
	res.changes.append({"path": str(path), "action": "update" if path.exists() else "create", "diff": unified_diff(before, after, path.name), "_text": after})


def _load_fixture(app: App) -> list[dict]:
	p = _fixture_path(app)
	if not p.exists():
		return []
	data = json.loads(p.read_text(encoding="utf-8") or "[]")
	if not isinstance(data, list):
		raise FdeskError(f"{p} must be a JSON list of records")
	return data


def _plan_fixture(res: Result, app: App, records: list[dict]) -> None:
	p = _fixture_path(app)
	if not records:
		_plan_text(res, p, "", "delete")
		return
	records = sorted(records, key=lambda r: r.get("name", ""))
	style = detect_style(p.read_text(encoding="utf-8")) if p.exists() else FIXTURE_STYLE
	_plan_text(res, p, render(records, style))  # render() accepts any JSON value


def _all_workspaces(app: App) -> list[tuple[Path, dict]]:
	out = []
	for p in sorted(app.package.glob("*/workspace/*/*.json")):
		try:
			doc = load(p)
		except ValueError:
			continue
		if doc.get("doctype") == "Workspace":
			out.append((p, doc))
	return out


def _find_workspace(app: App, ws: str) -> tuple[Path, dict]:
	for p, doc in _all_workspaces(app):
		if ws in (doc.get("name"), doc.get("label"), doc.get("title"), p.stem) or scrub(ws) == p.stem:
			return p, doc
	raise FdeskError(f"workspace {ws!r} not found in app {app.name}")


def _content(doc: dict) -> list[dict]:
	raw = doc.get("content")
	if not raw:
		return []
	try:
		val = json.loads(raw)
	except ValueError:
		raise FdeskError(f"workspace {doc.get('name')!r} has unparseable content JSON")
	return val if isinstance(val, list) else []


def _wired(doc: dict, name: str) -> str | None:
	"""Block id when content has a custom_block for `name`, else None."""
	for b in _content(doc):
		if b.get("type") == "custom_block" and (b.get("data") or {}).get("custom_block_name") == name:
			return b.get("id")
	return None


# ---------------------------------------------------------------- hooks.py fixtures editing
def _offsets(text: str):
	"""(lineno, utf8 col) -> char offset."""
	starts, pos = [0], 0
	for line in text.splitlines(True):
		pos += len(line)
		starts.append(pos)
	lines = text.splitlines(True)

	def off(lineno, col):
		line = lines[lineno - 1]
		return starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8"))

	return off


def _entry_literal(names: list[str], shape: str) -> str:
	n = json.dumps(names)
	if shape == "dict":
		return f'{{"dt": "{DT}", "filters": {{"name": ["in", {n}]}}}}'
	return f'{{"dt": "{DT}", "filters": [["name", "in", {n}]]}}'


def _entry_names(d: dict) -> tuple[list[str] | None, str]:
	"""Names in an existing Custom HTML Block fixture dict; (None, ..) when it exports every block."""
	extra = set(d) - {"dt", "filters"}
	if extra:
		raise FdeskError(f"hooks.py fixtures: the {DT!r} entry has unsupported keys {sorted(extra)}; edit hooks.py by hand")
	f = d.get("filters")
	if f is None:
		return None, "list"
	if isinstance(f, dict) and set(f) == {"name"} and isinstance(f["name"], list) and len(f["name"]) == 2 and f["name"][0] == "in" and isinstance(f["name"][1], list):
		return list(f["name"][1]), "dict"
	if isinstance(f, list) and len(f) == 1 and isinstance(f[0], list) and len(f[0]) == 3 and f[0][0] == "name" and f[0][1] == "in" and isinstance(f[0][2], list):
		return list(f[0][2]), "list"
	raise FdeskError(f"hooks.py fixtures: cannot parse the filters of the {DT!r} entry ({f!r}); expected [[\"name\", \"in\", [...]]]")


def edit_hooks(text: str, update) -> tuple[str, list[str]]:
	"""Return (new_text, warnings). `update(existing_names) -> new_names`; empty result removes our entry."""
	warnings: list[str] = []
	try:
		tree = ast.parse(text)
	except SyntaxError as e:
		raise FdeskError(f"hooks.py does not parse: {e}")
	assigns = [n for n in tree.body if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "fixtures" for t in n.targets))
		or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "fixtures")]
	if len(assigns) > 1:
		raise FdeskError("hooks.py assigns `fixtures` more than once; edit it by hand")
	if not assigns:
		names = update([])
		if not names:
			return text, warnings
		sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") or not text else "\n\n")
		new = text + sep + f"fixtures = [\n\t{_entry_literal(names, 'list')},\n]\n"
		return new, warnings
	node = assigns[0]
	lst = node.value
	if not isinstance(lst, ast.List):
		raise FdeskError("hooks.py: `fixtures` is not a plain list literal (found "
			f"{type(lst).__name__}); add the {DT!r} entry by hand")
	off = _offsets(text)
	ours = None
	for elt in lst.elts:
		if isinstance(elt, ast.Constant) and elt.value == DT:
			ours = (elt, None, "list")
			break
		if isinstance(elt, ast.Dict):
			try:
				d = ast.literal_eval(elt)
			except ValueError:
				continue
			if d.get("dt") == DT:
				try:
					ex, shape = _entry_names(d)
				except FdeskError:
					raise
				ours = (elt, ex, shape)
				break
	if ours is None:
		names = update([])
		if not names:
			return text, warnings
		entry = _entry_literal(names, "list")
		if not lst.elts:
			s, e = off(lst.lineno, lst.col_offset), off(lst.end_lineno, lst.end_col_offset)
			return text[:s] + f"[\n\t{entry},\n]" + text[e:], warnings
		last = lst.elts[-1]
		le = off(last.end_lineno, last.end_col_offset)
		multiline = lst.lineno != lst.end_lineno
		rest = text[le:]
		has_comma = rest.lstrip(" \t").startswith(",")
		if multiline:
			indent = re.match(r"[ \t]*", text.splitlines()[last.lineno - 1]).group(0) or "\t"
			if has_comma:
				ci = le + rest.index(",") + 1
				return text[:ci] + f"\n{indent}{entry}," + text[ci:], warnings
			return text[:le] + f",\n{indent}{entry}," + text[le:], warnings
		return text[:le] + f", {entry}" + text[le:], warnings
	elt, existing, shape = ours
	if existing is None:
		warnings.append(f"hooks.py already exports every {DT} (no name filter); left unchanged")
		return text, warnings
	new_names = update(list(existing))
	if set(new_names) == set(existing):
		return text, warnings
	s, e = off(elt.lineno, elt.col_offset), off(elt.end_lineno, elt.end_col_offset)
	if new_names:
		return text[:s] + _entry_literal(new_names, shape) + text[e:], warnings
	# remove our (now empty) entry
	ls = text.rfind("\n", 0, s) + 1
	whole_line = text[ls:s].strip() == ""
	tail = text[e:]
	m = re.match(r"[ \t]*,?[ \t]*(#[^\n]*)?\n?", tail) if whole_line else re.match(r"[ \t]*,?[ \t]*", tail)
	return text[: ls if whole_line else s] + text[e + (m.end() if m else 0):], warnings


def _plan_hooks(res: Result, app: App, update) -> None:
	p = app.package / "hooks.py"
	if not p.exists():
		raise FdeskError(f"{p} not found")
	before = p.read_text(encoding="utf-8")
	after, warns = edit_hooks(before, update)
	res.warnings.extend(warns)
	_plan_text(res, p, after)


# ---------------------------------------------------------------- commands
def _parse_params(items) -> dict:
	out = {}
	for it in items or []:
		if "=" not in it:
			raise FdeskError(f"--param expects KEY=VALUE, got {it!r}")
		k, v = it.split("=", 1)
		out[k.strip()] = v
	return out


def _roles(raw: str | None) -> list[dict]:
	if not raw:
		return []
	roles = []
	for r in (x.strip() for x in raw.split(",")):
		if r and {"role": r} not in roles:
			roles.append({"role": r})
	return roles


def cmd_new(args) -> Result:
	app = App.find(args.app)
	name = (args.name or "").strip()
	if not name or "/" in name or '"' in name:
		raise FdeskError("--name must be non-empty and contain no '/' or quotes")
	if args.kind not in KINDS:
		raise FdeskError(f"--kind must be one of {KINDS}")
	col = args.col if args.col is not None else 12
	if not 1 <= col <= 12:
		raise FdeskError("--col must be 1..12")
	title = args.title or DEFAULT_TITLES[args.kind]
	ws_path, ws_doc = _find_workspace(app, args.workspace)
	record = {"doctype": DT, "name": name, "private": 0, "roles": _roles(args.roles),
		**render_template(args.kind, title, _parse_params(getattr(args, "param", None)))}
	problems = lint_block(record)
	if problems:
		raise FdeskError("generated block fails lint: " + "; ".join(problems))
	res = Result()
	records = _load_fixture(app)
	existing = next((r for r in records if r.get("name") == name), None)
	if existing is not None and {k: existing.get(k) for k in record} != record:
		raise FdeskError(f"custom block {name!r} already exists with different content/kind/roles; run `block remove --name {name!r}` first")
	if existing is None:
		records.append(record)
	_plan_fixture(res, app, records)
	names = sorted({r["name"] for r in records})
	_plan_hooks(res, app, lambda ex: sorted(set(ex) | set(names)))
	block_id = _wired(ws_doc, name)
	if block_id is None:
		try:
			block_id = workspace.add_block(ws_doc, "custom_block", child_row={"custom_block_name": name, "label": title},
				data={"custom_block_name": name, "col": col})
		except NotImplementedError:
			raise FdeskError("workspace.add_block is not implemented yet")
		plan_write(res, ws_path, ws_doc)
	res.info.update({"block": name, "kind": args.kind, "workspace": ws_doc.get("name"), "block_id": block_id})
	return res


def _wired_in(app: App, name: str) -> list[str]:
	return [d.get("name") for _, d in _all_workspaces(app) if _wired(d, name)]


def cmd_list(args) -> Result:
	app = App.find(args.app)
	rows = [{"name": r.get("name"), "workspaces": _wired_in(app, r.get("name")), "roles": [x.get("role") for x in r.get("roles", [])],
		"problems": lint_block(r)} for r in _load_fixture(app)]
	res = Result()
	res.info["blocks"] = rows
	if not getattr(args, "json", False):
		for r in rows:
			state = "ok" if not r["problems"] else f"{len(r['problems'])} lint problem(s)"
			print(f"{r['name']}\tworkspaces={','.join(r['workspaces']) or '-'}\troles={','.join(r['roles']) or '-'}\t{state}")
		if not rows:
			print("no custom blocks")
	return res


def _unwire(doc: dict, name: str) -> bool:
	blocks = _content(doc)
	keep = [b for b in blocks if not (b.get("type") == "custom_block" and (b.get("data") or {}).get("custom_block_name") == name)]
	rows = [r for r in doc.get("custom_blocks", []) if r.get("custom_block_name") != name]
	if len(keep) == len(blocks) and len(rows) == len(doc.get("custom_blocks", [])):
		return False
	doc["content"] = json.dumps(keep)
	doc["custom_blocks"] = rows
	return True


def cmd_remove(args) -> Result:
	app = App.find(args.app)
	name = args.name
	res = Result()
	records = _load_fixture(app)
	found = any(r.get("name") == name for r in records)
	if found:
		records = [r for r in records if r.get("name") != name]
		_plan_fixture(res, app, records)
		_plan_hooks(res, app, lambda ex: sorted(set(ex) - {name}))
	targets = [_find_workspace(app, args.workspace)] if args.workspace else _all_workspaces(app)
	touched = 0
	for p, doc in targets:
		if _unwire(doc, name):
			plan_write(res, p, doc)
			touched += 1
	if not found and not touched:
		res.warnings.append(f"custom block {name!r} not found in fixtures or any workspace; nothing to do")
	elif args.workspace and not touched:
		res.warnings.append(f"block {name!r} is not wired into workspace {args.workspace!r}")
	return res


def preview_html(record: dict) -> str:
	tpl = _read("_preview.html")
	payload = {k: record.get(k) or "" for k in ("html", "style", "script")}
	title = htmllib.escape(record.get("name", "block"), quote=True)
	return tpl.replace("{{title}}", title).replace("{{block_json}}", _js_json(payload))


def cmd_preview(args) -> Result:
	app = App.find(args.app)
	record = next((r for r in _load_fixture(app) if r.get("name") == args.name), None)
	if record is None:
		raise FdeskError(f"custom block {args.name!r} not found in {_fixture_path(app)}")
	page = preview_html(record)
	if getattr(args, "json", False):
		res = Result()
		res.info["html"] = page
		return res
	sys.stdout.write(page)
	sys.stdout.flush()
	raise SystemExit(0)


def register(sub):
	g = sub.add_parser("block", help="Custom HTML Blocks (fixture + workspace wiring)")
	gs = g.add_subparsers(dest="cmd", required=True)
	p = gs.add_parser("new", help="create a block from a template and wire it into a workspace")
	p.add_argument("--kind", required=True, choices=KINDS)
	p.add_argument("--name", required=True)
	p.add_argument("--workspace", required=True)
	p.add_argument("--title")
	p.add_argument("--col", type=int)
	p.add_argument("--roles")
	p.add_argument("--param", action="append", metavar="KEY=VALUE", help="template parameter (repeatable), e.g. doctype=Sales\\ Invoice filters='{\"docstatus\":1}'")
	p.set_defaults(func=cmd_new)
	p = gs.add_parser("list", help="list blocks in the app fixture")
	p.set_defaults(func=cmd_list)
	p = gs.add_parser("remove", help="remove a block from fixtures, hooks.py and workspaces")
	p.add_argument("--name", required=True)
	p.add_argument("--workspace")
	p.set_defaults(func=cmd_remove)
	p = gs.add_parser("render-preview", help="print a standalone HTML preview to stdout")
	p.add_argument("--name", required=True)
	p.set_defaults(func=cmd_preview)
