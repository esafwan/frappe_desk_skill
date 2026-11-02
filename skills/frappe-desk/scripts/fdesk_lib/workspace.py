"""Workspace edits (Workspace.content JSON string <-> child tables).

Facts (frappe v16 source):
- content blocks: frappe/public/js/frappe/views/workspace/blocks/*.js (data key per type, e.g. shortcut_name)
- blocks bind to child rows by row label (falls back to the name field): frappe/desk/desktop.py (widget label matching)
- child tables: frappe/desk/doctype/workspace_{shortcut,link,chart,number_card,quick_list,custom_block}
"""

from __future__ import annotations

import copy
import hashlib
import json

from .common import App, FdeskError, Result, entity_path, plan_write

# block type -> (data key naming the child row, child table, row field holding the target name)
BLOCKS = {
	"shortcut": ("shortcut_name", "shortcuts", "link_to"),
	"card": ("card_name", "links", None),
	"chart": ("chart_name", "charts", "chart_name"),
	"number_card": ("number_card_name", "number_cards", "number_card_name"),
	"quick_list": ("quick_list_name", "quick_lists", "document_type"),
	"custom_block": ("custom_block_name", "custom_blocks", "custom_block_name"),
	"onboarding": ("onboarding_name", None, None),
}
PLAIN = ("header", "paragraph", "spacer")
ALL_TYPES = PLAIN + tuple(BLOCKS)


def content_blocks(doc: dict) -> list[dict]:
	"""Parsed doc['content'] (list of {id,type,data}); [] when empty."""
	raw = doc.get("content")
	if not raw:
		return []
	try:
		out = json.loads(raw) if isinstance(raw, str) else list(raw)
	except ValueError as e:
		raise FdeskError(f"workspace content is not valid JSON: {e}")
	if not isinstance(out, list):
		raise FdeskError("workspace content must be a JSON list of blocks")
	return out


def _dump_content(doc: dict, blocks: list[dict]) -> None:
	old = doc.get("content") or ""
	spaced = '": ' in old  # keep the hand-written style; Frappe's own editor writes compact JSON
	doc["content"] = json.dumps(blocks) if spaced else json.dumps(blocks, separators=(",", ":"))


def _row_label(row: dict, name_field: str | None) -> str:
	return row.get("label") or (row.get(name_field) if name_field else None) or row.get("chart_name") or ""


def _new_id(blocks: list[dict], btype: str, label: str) -> str:
	"""Deterministic 10-char id from type+label+counter (stable across reruns, unique within the page)."""
	used = {b.get("id") for b in blocks}
	n = 0
	while True:
		bid = hashlib.sha1(f"{btype}|{label}|{n}".encode()).hexdigest()[:10]
		if bid not in used:
			return bid
		n += 1


def add_block(doc: dict, block_type: str, child_row: dict | None = None, data: dict | None = None, after_id: str | None = None) -> str:
	"""Append a content block of `block_type` (header|paragraph|spacer|shortcut|card|chart|number_card|quick_list|custom_block|onboarding)
	to doc['content'] (a JSON *string*), add `child_row` to the matching child table when given, return the new block id.
	Pure function on the dict: no I/O. Raises FdeskError on unknown type or duplicate label."""
	if block_type not in ALL_TYPES:
		raise FdeskError(f"unknown block type {block_type!r}; expected one of {list(ALL_TYPES)}")
	blocks = content_blocks(doc)
	data = dict(data or {})
	data.setdefault("col", 12)
	label = ""
	if block_type in BLOCKS:
		key, table, name_field = BLOCKS[block_type]
		if child_row is not None and table:
			label = _row_label(child_row, name_field)
			if not label:
				raise FdeskError(f"{block_type} row needs a label")
			if any(_row_label(r, name_field) == label for r in doc.get(table) or [] if r.get("type") != "Link"):
				raise FdeskError(f"{table} already has a row labelled {label!r}")
			data.setdefault(key, label)
		label = label or data.get(key, "")
		if not data.get(key):
			raise FdeskError(f"{block_type} block needs data[{key!r}]")
		if any(b.get("type") == block_type and b.get("data", {}).get(key) == data[key] for b in blocks):
			raise FdeskError(f"duplicate {block_type} block for {data[key]!r}")
		if child_row is not None and table:
			row = dict(child_row)
			if block_type == "card":
				row.setdefault("type", "Card Break")
			doc.setdefault(table, []).append(row)
	else:
		label = data.get("text", "")
		if block_type == "spacer":
			data = {k: v for k, v in data.items() if k != "text"}
	bid = _new_id(blocks, block_type, label)
	block = {"id": bid, "type": block_type, "data": data}
	if after_id:
		idx = next((i for i, b in enumerate(blocks) if b.get("id") == after_id), None)
		if idx is None:
			raise FdeskError(f"no block with id {after_id!r}")
		blocks.insert(idx + 1, block)
	else:
		blocks.append(block)
	_dump_content(doc, blocks)
	return bid


# ---------------------------------------------------------------- helpers

def _find(app: App, name: str):
	for mod in app.modules:
		p = entity_path(app, "workspace", name, mod)
		if p.exists():
			return p, json.loads(p.read_text(encoding="utf-8"))
	raise FdeskError(f"workspace {name!r} not found in app {app.name}")


def _edit(args, name, fn, **info) -> Result:
	"""Load workspace, apply fn(doc, res) on a copy, plan the write (no-op when unchanged)."""
	app = App.find(args.app)
	path, doc = _find(app, name)
	new = copy.deepcopy(doc)
	res = Result()
	extra = fn(new, res)
	plan_write(res, path, new)
	res.info.update(info)
	if extra:
		res.info.update(extra)
	return res


def _warn_missing(res, kind, name, app_name):
	try:
		from . import catalog
		if catalog.lookup(kind, name, app_name) is False:
			res.warnings.append(f"{kind} {name!r} not found in catalog")
	except Exception:
		pass


def _find_block(doc, btype, key, value):
	return next((b for b in content_blocks(doc) if b.get("type") == btype and b.get("data", {}).get(key) == value), None)


def _ensure_widget(doc, res, btype, row, col, after=None):
	"""Idempotently ensure child row + content block. Conflict = same label, different target."""
	key, table, name_field = BLOCKS[btype]
	label = _row_label(row, name_field)
	existing = next((r for r in doc.get(table) or [] if r.get("type") != "Card Break" and _row_label(r, name_field) == label), None)
	if existing is not None:
		for k, v in row.items():
			if v not in (None, "") and existing.get(k) not in (None, "") and existing.get(k) != v:
				raise FdeskError(f"{table} already has {label!r} with {k}={existing.get(k)!r} (requested {v!r})")
		if _find_block(doc, btype, key, label):
			return {}
		return {"block_id": add_block(doc, btype, None, {key: label, "col": col}, after)}
	return {"block_id": add_block(doc, btype, row, {key: label, "col": col}, after)}


def _json_arg(text, what):
	if text is None:
		return None
	try:
		json.loads(text)
	except ValueError as e:
		raise FdeskError(f"{what} is not valid JSON: {e}")
	return text


# ---------------------------------------------------------------- commands

def cmd_new(args) -> Result:
	app = App.find(args.app)
	label = args.label or args.name
	path = entity_path(app, "workspace", args.name, args.module)
	spec = {"label": label, "title": label, "module": args.module, "type": "Workspace", "icon": args.icon,
		"standard": 1, "public": 1, "name": args.name}
	res = Result()
	if path.exists():
		cur = json.loads(path.read_text(encoding="utf-8"))
		for k, v in spec.items():
			if cur.get(k) != v:
				raise FdeskError(f"workspace {args.name!r} exists with {k}={cur.get(k)!r} (requested {v!r})")
		return res
	doc = {**spec, "doctype": "Workspace", "docstatus": 0, "owner": "Administrator", "modified_by": "Administrator",
		"is_hidden": 0, "parent_page": "", "content": "[]", "charts": [], "custom_blocks": [], "links": [],
		"number_cards": [], "quick_lists": [], "roles": [], "shortcuts": []}
	from datetime import datetime
	doc["creation"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
	plan_write(res, path, doc)
	return res


def cmd_add_shortcut(args) -> Result:
	if args.type == "URL":
		if not args.url:
			raise FdeskError("--url is required for type URL")
	elif not args.to:
		raise FdeskError(f"--to is required for type {args.type}")
	if args.doc_view and args.type != "DocType":
		raise FdeskError("--doc-view only applies to type DocType")
	row = {"type": args.type, "label": args.label}
	if args.type == "URL":
		row["url"] = args.url
	else:
		row["link_to"] = args.to
	if args.type == "DocType":
		row["doc_view"] = args.doc_view or "List"
	if args.color:
		row["color"] = args.color
	if args.stats_filter:
		row["stats_filter"] = _json_arg(args.stats_filter, "--stats-filter")

	def fn(doc, res):
		if args.type != "URL":
			_warn_missing(res, args.type, args.to, args.app)
		return _ensure_widget(doc, res, "shortcut", row, args.col, getattr(args, "after", None))
	return _edit(args, args.workspace, fn)


def _remove_block(doc, btype, key, label):
	blocks = [b for b in content_blocks(doc) if not (b.get("type") == btype and b.get("data", {}).get(key) == label)]
	_dump_content(doc, blocks)


def cmd_remove_shortcut(args) -> Result:
	def fn(doc, res):
		rows = doc.get("shortcuts") or []
		if not any(r.get("label") == args.label for r in rows):
			return
		doc["shortcuts"] = [r for r in rows if r.get("label") != args.label]
		_remove_block(doc, "shortcut", "shortcut_name", args.label)
	return _edit(args, args.workspace, fn)


def cmd_add_link_card(args) -> Result:
	def fn(doc, res):
		row = {"type": "Card Break", "label": args.label, "hidden": 0, "link_count": 0, "onboard": 0, "is_query_report": 0}
		if args.icon:
			row["icon"] = args.icon
		ex = next((r for r in doc.get("links") or [] if r.get("type") == "Card Break" and r.get("label") == args.label), None)
		if ex is not None:
			if args.icon and ex.get("icon") not in (None, "", args.icon):
				raise FdeskError(f"card {args.label!r} exists with icon {ex.get('icon')!r}")
			if _find_block(doc, "card", "card_name", args.label):
				return
			return {"block_id": add_block(doc, "card", None, {"card_name": args.label, "col": args.col})}
		return {"block_id": add_block(doc, "card", row, {"card_name": args.label, "col": args.col})}
	return _edit(args, args.workspace, fn)


def _card_span(links, card):
	"""(start, end) indices of the Card Break row and one past its last Link row."""
	start = next((i for i, r in enumerate(links) if r.get("type") == "Card Break" and r.get("label") == card), None)
	if start is None:
		raise FdeskError(f"no link card {card!r}; create it with add-link-card first")
	end = start + 1
	while end < len(links) and links[end].get("type") != "Card Break":
		end += 1
	return start, end


def _recount(links, start, end):
	links[start]["link_count"] = end - start - 1  # frappe/desk/doctype/workspace_link: link_count = Link rows under the break


def cmd_add_link(args) -> Result:
	def fn(doc, res):
		links = doc.setdefault("links", [])
		start, end = _card_span(links, args.card)
		new = {"type": "Link", "label": args.label, "link_type": args.link_type, "link_to": args.to,
			"hidden": 0, "link_count": 0, "onboard": 1 if args.onboard else 0, "is_query_report": 1 if args.is_query_report else 0}
		if args.dependencies:
			new["dependencies"] = args.dependencies
		if args.link_type == "Dashboard":
			res.warnings.append("Workspace Link.link_type only allows DocType|Page|Report in v16; Dashboard may fail validation")
		ex = next((r for r in links[start + 1:end] if r.get("label") == args.label), None)
		if ex is not None:
			for k in ("link_type", "link_to"):
				if ex.get(k) != new[k]:
					raise FdeskError(f"link {args.label!r} in card {args.card!r} exists with {k}={ex.get(k)!r} (requested {new[k]!r})")
			_recount(links, start, end)
			return
		_warn_missing(res, args.link_type, args.to, args.app)
		links.insert(end, new)
		_recount(links, start, end + 1)
	return _edit(args, args.workspace, fn)


def cmd_remove_link(args) -> Result:
	def fn(doc, res):
		links = doc.get("links") or []
		start, end = _card_span(links, args.card)
		idx = next((i for i in range(start + 1, end) if links[i].get("label") == args.label), None)
		if idx is None:
			return
		del links[idx]
		_recount(links, start, end - 1)
	return _edit(args, args.workspace, fn)


def _text_block(args, btype, html, after=None):
	def fn(doc, res):
		if any(b.get("type") == btype and b.get("data", {}).get("text") == html for b in content_blocks(doc)):
			return
		return {"block_id": add_block(doc, btype, None, {"text": html, "col": 12}, after)}
	return _edit(args, args.workspace, fn)


def cmd_add_header(args) -> Result:
	return _text_block(args, "header", f'<span class="h4"><b>{args.text}</b></span>', args.after)


def cmd_add_paragraph(args) -> Result:
	return _text_block(args, "paragraph", args.text, getattr(args, "after", None))


def cmd_add_spacer(args) -> Result:
	def fn(doc, res):
		blocks = content_blocks(doc)
		if not args.after and blocks and blocks[-1].get("type") == "spacer":
			return  # a trailing spacer already exists: rerun is a no-op
		return {"block_id": add_block(doc, "spacer", None, {"col": 12}, args.after)}
	return _edit(args, args.workspace, fn)


def cmd_add_chart(args) -> Result:
	row = {"chart_name": args.chart, "label": args.label or args.chart}
	def fn(doc, res):
		_warn_missing(res, "Dashboard Chart", args.chart, args.app)
		return _ensure_widget(doc, res, "chart", row, args.col)
	return _edit(args, args.workspace, fn)


def cmd_add_number_card(args) -> Result:
	row = {"number_card_name": args.card, "label": args.label or args.card}
	def fn(doc, res):
		_warn_missing(res, "Number Card", args.card, args.app)
		return _ensure_widget(doc, res, "number_card", row, args.col)
	return _edit(args, args.workspace, fn)


def cmd_add_quick_list(args) -> Result:
	row = {"document_type": args.doctype, "label": args.label}
	def fn(doc, res):
		_warn_missing(res, "DocType", args.doctype, args.app)
		return _ensure_widget(doc, res, "quick_list", row, args.col)
	return _edit(args, args.workspace, fn)


def cmd_set_icon(args) -> Result:
	def fn(doc, res):
		doc["icon"] = args.icon
	return _edit(args, args.workspace, fn)


def cmd_set_roles(args) -> Result:
	roles = [r.strip() for r in args.roles.split(",") if r.strip()]
	def fn(doc, res):
		if [r.get("role") for r in doc.get("roles") or []] != roles:
			doc["roles"] = [{"role": r} for r in roles]
	return _edit(args, args.workspace, fn)


def cmd_hide(args) -> Result:
	def fn(doc, res):
		doc["is_hidden"] = 0 if args.unhide else 1
	return _edit(args, args.workspace, fn)


def cmd_move_block(args) -> Result:
	def fn(doc, res):
		blocks = content_blocks(doc)
		ids = [b.get("id") for b in blocks]
		for i in (args.id, args.after):
			if i not in ids:
				raise FdeskError(f"no block with id {i!r}")
		if args.id == args.after:
			raise FdeskError("--id and --after must differ")
		blk = blocks.pop(ids.index(args.id))
		blocks.insert([b.get("id") for b in blocks].index(args.after) + 1, blk)
		if [b.get("id") for b in blocks] != ids:
			_dump_content(doc, blocks)
	return _edit(args, args.workspace, fn)


def cmd_list(args) -> Result:
	app = App.find(args.app)
	_, doc = _find(app, args.workspace)
	res = Result()
	res.info = {
		"blocks": content_blocks(doc),
		"shortcuts": [r.get("label") for r in doc.get("shortcuts") or []],
		"cards": {r["label"]: [x.get("label") for x in _links_of(doc, r["label"])] for r in doc.get("links") or [] if r.get("type") == "Card Break"},
		"charts": [r.get("label") or r.get("chart_name") for r in doc.get("charts") or []],
		"number_cards": [r.get("label") or r.get("number_card_name") for r in doc.get("number_cards") or []],
		"quick_lists": [r.get("label") for r in doc.get("quick_lists") or []],
		"roles": [r.get("role") for r in doc.get("roles") or []],
		"is_hidden": doc.get("is_hidden"),
		"icon": doc.get("icon"),
	}
	return res


def _links_of(doc, card):
	s, e = _card_span(doc["links"], card)
	return doc["links"][s + 1:e]


def register(sub):
	g = sub.add_parser("workspace", help="edit Workspace JSON")
	gs = g.add_subparsers(dest="cmd", required=True)

	def cmd(name, func, ws=True):
		p = gs.add_parser(name)
		if ws:
			p.add_argument("workspace")
		p.set_defaults(func=func)
		return p

	p = cmd("new", cmd_new, ws=False)
	p.add_argument("name")
	p.add_argument("--module", required=True)
	p.add_argument("--icon", required=True)
	p.add_argument("--label")
	p.set_defaults(func=cmd_new)

	p = cmd("add-shortcut", cmd_add_shortcut)
	p.add_argument("--type", required=True, choices=["DocType", "Report", "Page", "Dashboard", "URL"])
	p.add_argument("--to")
	p.add_argument("--url")
	p.add_argument("--label", required=True)
	p.add_argument("--doc-view", choices=["List", "Report Builder", "Dashboard", "Tree", "New", "Calendar", "Kanban", "Image"])
	p.add_argument("--color")
	p.add_argument("--stats-filter")
	p.add_argument("--col", type=int, default=3)
	p.add_argument("--after")

	p = cmd("remove-shortcut", cmd_remove_shortcut)
	p.add_argument("--label", required=True)

	p = cmd("add-link-card", cmd_add_link_card)
	p.add_argument("--label", required=True)
	p.add_argument("--icon")
	p.add_argument("--col", type=int, default=4)

	p = cmd("add-link", cmd_add_link)
	p.add_argument("--card", required=True)
	p.add_argument("--link-type", required=True, choices=["DocType", "Report", "Page", "Dashboard"])
	p.add_argument("--to", required=True)
	p.add_argument("--label", required=True)
	p.add_argument("--onboard", action="store_true")
	p.add_argument("--dependencies")
	p.add_argument("--is-query-report", action="store_true")

	p = cmd("remove-link", cmd_remove_link)
	p.add_argument("--card", required=True)
	p.add_argument("--label", required=True)

	p = cmd("add-header", cmd_add_header)
	p.add_argument("--text", required=True)
	p.add_argument("--after")

	p = cmd("add-paragraph", cmd_add_paragraph)
	p.add_argument("--text", required=True)
	p.add_argument("--after")

	p = cmd("add-spacer", cmd_add_spacer)
	p.add_argument("--after")

	p = cmd("add-chart", cmd_add_chart)
	p.add_argument("--chart", required=True)
	p.add_argument("--label")
	p.add_argument("--col", type=int, default=12)

	p = cmd("add-number-card", cmd_add_number_card)
	p.add_argument("--card", required=True)
	p.add_argument("--label")
	p.add_argument("--col", type=int, default=4)

	p = cmd("add-quick-list", cmd_add_quick_list)
	p.add_argument("--doctype", required=True)
	p.add_argument("--label", required=True)
	p.add_argument("--col", type=int, default=6)

	p = cmd("set-icon", cmd_set_icon)
	p.add_argument("--icon", required=True)

	p = cmd("set-roles", cmd_set_roles)
	p.add_argument("--roles", required=True)

	p = cmd("hide", cmd_hide)
	p.add_argument("--unhide", action="store_true")

	p = cmd("move-block", cmd_move_block)
	p.add_argument("--id", required=True)
	p.add_argument("--after", required=True)

	cmd("list", cmd_list)
