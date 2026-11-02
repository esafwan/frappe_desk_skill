"""Sidebar edits. Facts: frappe/desk/doctype/sidebar_item/sidebar_item.json and
ury_workspaces/tracks/sa-v16-workspaces-ia/research/erpnext-sidebar-mechanics.md (sections 1.1, 1.2, 3)."""

from __future__ import annotations

import copy
import json
import re

from .common import App, FdeskError, Result, entity_path, load, now_modified, plan_write, scrub

# sidebar_item.json: link_type options (the Select enum)
LINK_TYPES = ["DocType", "Page", "Report", "Workspace", "Dashboard", "URL"]
_CATALOG_KIND = {"DocType": "doctype", "Page": "page", "Report": "report", "Workspace": "workspace", "Dashboard": "dashboard"}
# sidebar.py title rule: must not contain these characters
_BAD_TITLE = re.compile(r"[/?#%\\]")


# ---------- helpers ----------

def _label(app: App, path) -> str:
	try:
		return str(path.relative_to(app.root))
	except ValueError:
		return str(path)


def _find(app: App, name: str):
	s = scrub(name)
	hits = []
	for p in sorted(app.package.glob(f"**/sidebar/{s}/{s}.json")):
		try:
			doc = load(p)
		except ValueError:
			continue
		if doc.get("doctype") == "Sidebar" and name in (doc.get("name"), doc.get("title")):
			hits.append((p, doc))
	if not hits:
		known = sorted(d.get("name", "?") for p in app.package.glob("**/sidebar/*/*.json") for d in [_safe_load(p)] if d.get("doctype") == "Sidebar")
		raise FdeskError(f"sidebar {name!r} not found in app {app.name} (known: {known})")
	return hits[0]


def _safe_load(p):
	try:
		return load(p)
	except ValueError:
		return {}


def _norm_json(value, what):
	"""route_options/filters are JSON fields stored as strings; normalise so identity comparison is stable."""
	if value is None:
		return None
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError as e:
			raise FdeskError(f"{what} is not valid JSON: {e}")
	if not isinstance(value, (dict, list)):
		raise FdeskError(f"{what} must be a JSON object or array")
	return json.dumps(value, sort_keys=True)


def _ident(it: dict):
	# sidebar.py LINKED_IDENTITY_FIELDS = (type, link_type, link_to, url, filters)
	f = it.get("filters")
	try:
		f = json.dumps(json.loads(f), sort_keys=True) if isinstance(f, str) and f else f or ""
	except ValueError:
		pass
	return (it.get("link_type"), it.get("link_to") or "", it.get("url") or "", f)


def _is_link(it):
	return it.get("type", "Link") == "Link"


def _is_break(it):
	return it.get("type") == "Section Break"


def _sections(items):
	return [it.get("label") for it in items if _is_break(it)]


def _section_idx(items, label):
	idx = [i for i, it in enumerate(items) if _is_break(it) and it.get("label") == label]
	if not idx:
		raise FdeskError(f"section {label!r} not found (sections: {_sections(items)})")
	if len(idx) > 1:
		raise FdeskError(f"section label {label!r} is ambiguous ({len(idx)} sections)")
	return idx[0]


def _region(items, section):
	"""(lo, hi) slice of rows belonging to a section (None = leading top-level rows)."""
	if section is None:
		for i, it in enumerate(items):
			if _is_break(it):
				return 0, i
		return 0, len(items)
	i = _section_idx(items, section)
	for j in range(i + 1, len(items)):
		if _is_break(items[j]):
			return i + 1, j
	return i + 1, len(items)


def _find_in(items, lo, hi, label):
	for i in range(lo, hi):
		if items[i].get("label") == label:
			return i
	return None


def _locate(items, label, section=None):
	"""Index of the unique non-section row with `label` (restricted to `section` when given)."""
	if section is not None:
		lo, hi = _region(items, section)
		rng = range(lo, hi)
	else:
		rng = range(len(items))
	hits = [i for i in rng if items[i].get("label") == label and not _is_break(items[i])]
	if len(hits) > 1:
		raise FdeskError(f"label {label!r} is ambiguous ({len(hits)} rows); pass --section")
	return hits[0] if hits else None


def _insert_at(items, section, after, label_for_msg):
	lo, hi = _region(items, section)
	if after is None:
		return hi
	j = _find_in(items, lo, hi, after)
	if j is None:
		raise FdeskError(f"--after {after!r} not found in {'section ' + repr(section) if section else 'top level'}")
	return j + 1


def _warn_icon(res, icon, context, app):
	if not icon:
		return
	from . import icons
	try:
		ok = icons.exists(icon, context, app)
	except NotImplementedError:
		ok = None
	if ok is False:
		res.warnings.append(f"icon {icon!r} not found for {context} context")
	elif ok is None:
		res.warnings.append(f"icon {icon!r} could not be verified (no sprite found)")


def _warn_target(res, link_type, to, app):
	kind = _CATALOG_KIND.get(link_type)
	if not kind or not to:
		return
	from . import catalog
	try:
		ok = catalog.lookup(kind, to, app)
	except NotImplementedError:
		ok = None
	if ok is False:
		res.warnings.append(f"{link_type} {to!r} not found in the catalog")
	elif ok is None:
		res.warnings.append(f"{link_type} {to!r} could not be verified (catalog unavailable)")


def _finish(app, path, doc, new, res):
	plan_write(res, path, new, _label(app, path))
	return res


def _row_summary(it):
	d = {"type": it.get("type", "Link"), "label": it.get("label")}
	for k in ("link_type", "link_to", "url", "icon", "child", "indent", "is_default_module", "hidden"):
		if it.get(k) not in (None, "", 0):
			d[k] = it[k]
	return d


# ---------- commands ----------

def cmd_add_item(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	lt = args.link_type
	if lt not in LINK_TYPES:
		raise FdeskError(f"--link-type must be one of {LINK_TYPES}")
	to, url = getattr(args, "to", None), getattr(args, "url", None)
	if lt == "URL":
		if not url or to:
			raise FdeskError("a URL item needs --url (not --to)")  # URL items use `url`, not link_to
		if args.default_module:
			raise FdeskError("--default-module does not apply to URL items")
	else:
		if not to or url:
			raise FdeskError(f"a {lt} item needs --to (not --url)")
		if getattr(args, "open_in_new_tab", False):
			raise FdeskError("--open-in-new-tab applies to URL items only")
	nav = getattr(args, "navigate_to_tab", None)
	if nav is True:
		raise FdeskError("--navigate-to-tab needs a tab name (Autocomplete of the DocType's form tabs)")
	if nav and lt != "DocType":
		raise FdeskError("--navigate-to-tab applies to DocType items only")
	label = args.label or to or url
	items = (new := copy.deepcopy(doc)).setdefault("items", [])
	section = args.section
	lo, hi = _region(items, section) if section is not None else (0, 0)
	if section is not None:
		_section_idx(items, section)
	row = {"type": "Link", "label": label, "link_type": lt, "child": 0 if section is None else 1, "indent": 0}
	if lt == "URL":
		row["url"] = url
		if getattr(args, "open_in_new_tab", False):
			row["open_in_new_tab"] = 1
	else:
		row["link_to"] = to
	if args.icon:
		row["icon"] = args.icon
	if args.default_module:
		row["is_default_module"] = 1
	for key, val in (("route_options", getattr(args, "route_options", None)), ("filters", getattr(args, "filters", None))):
		v = _norm_json(val, "--" + key.replace("_", "-"))
		if v:
			row[key] = v
	if nav:
		row["navigate_to_tab"] = nav
	if args.hidden:
		row["hidden"] = 1
	res = Result()
	ident = _ident(row)
	lo, hi = _region(items, section)
	for i, it in enumerate(items):
		if not _is_link(it) or _ident(it) != ident:
			continue
		if lo <= i < hi and it.get("label") == label:
			res.info["noop"] = "item already present"
			return res  # identical request: no-op
		raise FdeskError(f"link {lt} {to or url!r} already exists in sidebar {doc['name']!r} as {it.get('label')!r}; refusing duplicate")
	j = _find_in(items, lo, hi, label)
	if j is not None:
		raise FdeskError(f"label {label!r} already exists in this section with a different target")
	if section is not None and not any(it.get("label") == "Overview" for it in items[lo:hi]):
		res.warnings.append(f"section {section!r} has no Overview child (each section should start with one)")
	if args.hidden:
		res.warnings.append("hidden only has effect inside a Custom Sidebar")
	pos = _insert_at(items, section, args.after, label)
	items.insert(pos, row)
	_warn_icon(res, args.icon, "sidebar", app)
	if lt != "URL":
		_warn_target(res, lt, to, app)
	return _finish(app, path, doc, new, res)


def cmd_remove_item(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	res = Result()
	i = _locate(items, args.label, args.section)
	if i is None:
		res.warnings.append(f"no item {args.label!r}; nothing to remove")
		return res
	items.pop(i)
	return _finish(app, path, doc, new, res)


def cmd_move_item(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	_section_idx(items, args.section)
	i = _locate(items, args.label)
	if i is None:
		raise FdeskError(f"item {args.label!r} not found")
	if args.after == args.label:
		raise FdeskError("--after cannot be the item itself")
	row = items.pop(i)
	row["child"] = 1
	lo, hi = _region(items, args.section)
	if _find_in(items, lo, hi, args.label) is not None:
		raise FdeskError(f"section {args.section!r} already has an item labelled {args.label!r}")
	pos = _insert_at(items, args.section, args.after, args.label)
	items.insert(pos, row)
	return _finish(app, path, doc, new, Result())


def cmd_rename_item(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	res = Result()
	i = _locate(items, args.label)
	if i is None:
		if _locate(items, args.new_label) is not None:
			return res  # already renamed
		raise FdeskError(f"item {args.label!r} not found")
	if args.label == args.new_label:
		return res
	# sidebar.py identity: unlinked rows are keyed by a hash of type+label, so renames must stay unique per section
	sec = next((items[k].get("label") for k in range(i, -1, -1) if _is_break(items[k])), None) if items[i].get("child") else None
	lo, hi = _region(items, sec)
	if _find_in(items, lo, hi, args.new_label) is not None:
		raise FdeskError(f"label {args.new_label!r} already exists in that section")
	items[i]["label"] = args.new_label
	return _finish(app, path, doc, new, res)


def cmd_set_icon(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	res = Result()
	if args.header:
		new["header_icon"] = args.icon
	else:
		if not args.label:
			raise FdeskError("set-icon needs --label or --header")
		items = new.setdefault("items", [])
		if args.section is None:
			hits = [i for i, it in enumerate(items) if it.get("label") == args.label]
			if len(hits) > 1:
				raise FdeskError(f"label {args.label!r} is ambiguous; pass --section")
			i = hits[0] if hits else None
		else:
			lo, hi = _region(items, args.section)
			i = _find_in(items, lo, hi, args.label)
		if i is None:
			raise FdeskError(f"item {args.label!r} not found")
		if items[i].get("type") == "Spacer":
			raise FdeskError("spacers have no icon")
		items[i]["icon"] = args.icon
	_warn_icon(res, args.icon, "sidebar", app)
	return _finish(app, path, doc, new, res)


def cmd_add_section(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	res = Result()
	ow = getattr(args, "overview_workspace", None)
	existing = [i for i, it in enumerate(items) if _is_break(it) and it.get("label") == args.label]
	if existing:
		i = existing[0]
		if items[i].get("icon") != args.icon:
			raise FdeskError(f"section {args.label!r} exists with icon {items[i].get('icon')!r}; use set-icon")
		if ow:
			lo, hi = _region(items, args.label)
			ov = _find_in(items, lo, hi, "Overview")
			if ov is None:
				raise FdeskError(f"section {args.label!r} exists without an Overview child; add one with add-item")
			if items[ov].get("link_to") != ow:
				raise FdeskError(f"section {args.label!r} Overview already points to {items[ov].get('link_to')!r}")
		return res
	row = {"type": "Section Break", "label": args.label, "icon": args.icon, "indent": 1, "collapsible": 1, "keep_closed": 0}
	new_rows = [row]
	if ow:
		ident = _ident({"link_type": "Workspace", "link_to": ow})
		for it in items:
			if _is_link(it) and _ident(it) == ident:
				raise FdeskError(f"Workspace {ow!r} is already linked in this sidebar as {it.get('label')!r}")
		new_rows.append({"type": "Link", "label": "Overview", "link_type": "Workspace", "link_to": ow, "child": 1, "indent": 0})
		_warn_target(res, "Workspace", ow, app)
	else:
		res.warnings.append(f"section {args.label!r} has no Overview child (use --overview-workspace)")
	if args.after:
		i = _section_idx(items, args.after)
		_, pos = _region(items, args.after)
	else:
		pos = len(items)
	items[pos:pos] = new_rows
	_warn_icon(res, args.icon, "sidebar", app)
	return _finish(app, path, doc, new, res)


def cmd_remove_section(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	res = Result()
	if args.label not in _sections(items):
		res.warnings.append(f"no section {args.label!r}; nothing to remove")
		return res
	i = _section_idx(items, args.label)
	lo, hi = _region(items, args.label)
	content = [it for it in items[lo:hi] if it.get("label") != "Overview"]
	if content and not args.force:
		raise FdeskError(f"section {args.label!r} still has {len(content)} item(s); pass --force to remove them too")
	del items[i:hi]
	return _finish(app, path, doc, new, res)


def cmd_reorder_sections(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	order = [s.strip() for s in args.order.split(",") if s.strip()]
	have = _sections(items)
	if sorted(order) != sorted(have) or len(set(order)) != len(order):
		raise FdeskError(f"--order must list every section exactly once; sidebar has {have}")
	if len(set(have)) != len(have):
		raise FdeskError("duplicate section labels; cannot reorder")
	first = next((i for i, it in enumerate(items) if _is_break(it)), len(items))
	head, blocks = items[:first], {}
	for s in have:
		lo, hi = _region(items, s)
		blocks[s] = items[lo - 1:hi]
	new["items"] = head + [r for s in order for r in blocks[s]]
	return _finish(app, path, doc, new, Result())


def cmd_add_spacer(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	res = Result()
	lo, hi = _region(items, args.section)
	pos = _insert_at(items, args.section, args.after, "spacer")
	# idempotent: a spacer already sits at that position
	if pos > lo and items[pos - 1].get("type") == "Spacer":
		return res
	# spacer identity is a hash of type+label, so each needs a distinct label
	taken = {it.get("label") for it in items}
	n = 1
	while f"Spacer {n}" in taken:
		n += 1
	items.insert(pos, {"type": "Spacer", "label": f"Spacer {n}", "child": 1, "indent": 0})
	return _finish(app, path, doc, new, res)


def cmd_list(args) -> Result:
	app = App.find(args.app)
	path, doc = _find(app, args.sidebar)
	items = doc.get("items", [])
	sections = []
	top = []
	cur = None
	for it in items:
		if _is_break(it):
			cur = {"label": it.get("label"), "icon": it.get("icon"), "indent": it.get("indent", 0), "items": []}
			sections.append(cur)
		else:
			(cur["items"] if cur is not None else top).append(_row_summary(it))
	info = {"sidebar": doc.get("name"), "path": str(path), "header_icon": doc.get("header_icon"), "module": doc.get("module"), "top": top, "sections": sections}
	res = Result(info=info)
	if not args.json:
		def fmt(r):
			tgt = r.get("link_to") or r.get("url") or ""
			return f"{r['label']}" + (f"  [{r.get('link_type')}: {tgt}]" if tgt else f"  ({r['type']})")
		print(f"{doc.get('name')} (header icon: {doc.get('header_icon')})")
		for r in top:
			print(f"  {fmt(r)}")
		for s in sections:
			print(f"  == {s['label']} (icon: {s['icon']})")
			for r in s["items"]:
				print(f"     - {fmt(r)}")
	return res


def cmd_new(args) -> Result:
	app = App.find(args.app)
	title = args.title
	if _BAD_TITLE.search(title):
		raise FdeskError("title must not contain / ? # % \\")
	rooted = bool(args.app_rooted)
	module = args.module
	if not rooted:
		if not module:
			raise FdeskError("--module is required unless --app-rooted")
		app.module_dir(module)  # validates against modules.txt
	elif module:
		app.module_dir(module)
	if title in app.modules and title != module:
		raise FdeskError(f"title {title!r} equals another Module Def name")
	path = entity_path(app, "sidebar", title, module, rooted)
	doc = {
		"app": app.name, "creation": now_modified(), "docstatus": 0, "doctype": "Sidebar",
		"header_icon": args.icon, "items": [], "modified_by": "Administrator", "name": title,
		"owner": "Administrator", "standard": 1, "title": title,
	}
	if not rooted:
		doc["module"] = module  # app-rooted sidebars leave module blank (sidebar.json: "Blank = app-rooted")
	res = Result()
	if path.exists():
		cur = load(path)
		if cur.get("header_icon") != args.icon or cur.get("module") != doc.get("module"):
			raise FdeskError(f"sidebar {title!r} already exists with different module/icon")
		return res
	_warn_icon(res, args.icon, "sidebar", app)
	return _finish(app, path, doc, doc, res)


def register(sub):
	g = sub.add_parser("sidebar", help="edit Sidebar JSON")
	gs = g.add_subparsers(dest="cmd", required=True)

	def sb(name, fn, help=None):
		p = gs.add_parser(name, help=help)
		p.add_argument("sidebar", help="sidebar name (title)")
		p.set_defaults(func=fn)
		return p

	p = sb("add-item", cmd_add_item)
	p.add_argument("--section", default=None)
	p.add_argument("--link-type", required=True, choices=LINK_TYPES)
	p.add_argument("--to")
	p.add_argument("--url")
	p.add_argument("--label")
	p.add_argument("--icon")
	p.add_argument("--after")
	p.add_argument("--default-module", action="store_true")
	p.add_argument("--route-options")
	p.add_argument("--filters")
	p.add_argument("--navigate-to-tab", nargs="?", const=True, default=None, metavar="TAB")
	p.add_argument("--open-in-new-tab", action="store_true")
	p.add_argument("--hidden", action="store_true")

	p = sb("remove-item", cmd_remove_item)
	p.add_argument("--label", required=True)
	p.add_argument("--section")

	p = sb("move-item", cmd_move_item)
	p.add_argument("--label", required=True)
	p.add_argument("--section", required=True)
	p.add_argument("--after")

	p = sb("rename-item", cmd_rename_item)
	p.add_argument("--label", required=True)
	p.add_argument("--new-label", required=True)

	p = sb("set-icon", cmd_set_icon)
	p.add_argument("--label")
	p.add_argument("--section")
	p.add_argument("--header", action="store_true")
	p.add_argument("--icon", required=True)

	p = sb("add-section", cmd_add_section)
	p.add_argument("--label", required=True)
	p.add_argument("--icon", required=True)
	p.add_argument("--after")
	p.add_argument("--overview-workspace")

	p = sb("remove-section", cmd_remove_section)
	p.add_argument("--label", required=True)
	p.add_argument("--force", action="store_true")

	p = sb("reorder-sections", cmd_reorder_sections)
	p.add_argument("--order", required=True)

	p = sb("add-spacer", cmd_add_spacer)
	p.add_argument("--section", required=True)
	p.add_argument("--after")

	sb("list", cmd_list)

	p = gs.add_parser("new")
	p.add_argument("title")
	p.add_argument("--module")
	p.add_argument("--icon", required=True)
	p.add_argument("--app-rooted", action="store_true")
	p.set_defaults(func=cmd_new)
