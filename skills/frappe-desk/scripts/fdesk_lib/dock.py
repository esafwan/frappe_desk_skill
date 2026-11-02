"""Dock edits (app rail). Facts: frappe/desk/doctype/dock + dock_item; file is app-rooted <app>/dock/<app>/<app>.json."""

from __future__ import annotations

import copy

from .common import App, FdeskError, Result, entity_path, load, now_modified, plan_write, scrub

LINK_TYPES = ["Sidebar", "Workspace", "URL"]  # dock_item.json link_type options


def _path(app: App):
	return entity_path(app, "dock", app.name)


def _load(app: App, create: bool):
	p = _path(app)
	if p.exists():
		return p, load(p)
	if not create:
		raise FdeskError(f"no dock file at {p}")
	doc = {
		"app": app.name, "creation": now_modified(), "docstatus": 0, "doctype": "Dock", "items": [],
		"modified_by": "Administrator", "name": app.name, "owner": "Administrator", "standard": 1,
	}
	return p, doc


def _label(app, p):
	try:
		return str(p.relative_to(app.root))
	except ValueError:
		return str(p)


def _warn_icon(res, icon, app):
	from . import icons
	try:
		ok = icons.exists(icon, "dock", app)
	except NotImplementedError:
		ok = None
	if ok is False:
		res.warnings.append(f"icon {icon!r} not found for dock context")
	elif ok is None:
		res.warnings.append(f"icon {icon!r} could not be verified (no sprite found)")


def _sidebar_known(app, name):
	s = scrub(name)
	return any(True for _ in app.package.glob(f"**/sidebar/{s}/{s}.json"))


def _target(it):
	return (it.get("link_type"), it.get("link_to") or it.get("url") or "")


def cmd_add_tile(args) -> Result:
	app = App.find(args.app)
	path, doc = _load(app, create=True)
	lt, to, url = args.link_type, getattr(args, "to", None), getattr(args, "url", None)
	if lt == "URL":
		if not url or to:
			raise FdeskError("a URL tile needs --url (not --to)")
	elif not to or url:
		raise FdeskError(f"a {lt} tile needs --to (not --url)")
	row = {"link_type": lt, "title": args.title, "icon": args.icon}
	row["url" if lt == "URL" else "link_to"] = url or to
	new = copy.deepcopy(doc)
	items = new.setdefault("items", [])
	res = Result()
	for it in items:
		same_t, same_target = it.get("title") == args.title, _target(it) == _target(row)
		if same_t and same_target:
			if it.get("icon") != args.icon:
				res.warnings.append(f"tile {args.title!r} exists with icon {it.get('icon')!r}; use set-icon")
			return res  # identical: no-op
		if same_t:
			raise FdeskError(f"tile titled {args.title!r} already points to {_target(it)[1]!r}")
		if same_target:
			raise FdeskError(f"{lt} {_target(row)[1]!r} is already a tile titled {it.get('title')!r}")
	pos = len(items)
	if args.after:
		idx = [i for i, it in enumerate(items) if it.get("title") == args.after]
		if not idx:
			raise FdeskError(f"--after {args.after!r} not found")
		pos = idx[0] + 1
	items.insert(pos, row)
	_warn_icon(res, args.icon, app)
	if lt == "Sidebar" and not _sidebar_known(app, to):
		from . import catalog
		try:
			ok = catalog.lookup("sidebar", to, app)
		except NotImplementedError:
			ok = None
		if ok is not True:
			res.warnings.append(f"Sidebar {to!r} not found in this app" + ("" if ok is None else " or the catalog"))
	elif lt == "Workspace":
		from . import catalog
		try:
			ok = catalog.lookup("workspace", to, app)
		except NotImplementedError:
			ok = None
		if ok is False:
			res.warnings.append(f"Workspace {to!r} not found in the catalog")
	plan_write(res, path, new, _label(app, path))
	return res


def cmd_remove_tile(args) -> Result:
	app = App.find(args.app)
	res = Result()
	path = _path(app)
	if not path.exists():
		res.warnings.append("no dock file; nothing to remove")
		return res
	_, doc = _load(app, False)
	new = copy.deepcopy(doc)
	before = len(new.get("items", []))
	new["items"] = [it for it in new.get("items", []) if it.get("title") != args.title]
	if len(new["items"]) == before:
		res.warnings.append(f"no tile {args.title!r}; nothing to remove")
		return res
	plan_write(res, path, new, _label(app, path))
	return res


def cmd_set_icon(args) -> Result:
	app = App.find(args.app)
	path, doc = _load(app, False)
	new = copy.deepcopy(doc)
	hit = [it for it in new.get("items", []) if it.get("title") == args.title]
	if not hit:
		raise FdeskError(f"tile {args.title!r} not found")
	hit[0]["icon"] = args.icon
	res = Result()
	_warn_icon(res, args.icon, app)
	plan_write(res, path, new, _label(app, path))
	return res


def cmd_list(args) -> Result:
	app = App.find(args.app)
	path = _path(app)
	tiles = load(path).get("items", []) if path.exists() else []
	res = Result(info={"path": str(path), "tiles": tiles})
	if not args.json:
		print(f"dock {app.name}: {len(tiles)} tile(s)")
		for t in tiles:
			print(f"  - {t.get('title')}  [{t.get('link_type')}: {t.get('link_to') or t.get('url')}]  icon={t.get('icon')}")
	return res


def register(sub):
	g = sub.add_parser("dock", help="edit the app Dock")
	gs = g.add_subparsers(dest="cmd", required=True)
	p = gs.add_parser("add-tile")
	p.add_argument("--link-type", required=True, choices=LINK_TYPES)
	p.add_argument("--to")
	p.add_argument("--url")
	p.add_argument("--title", required=True)
	p.add_argument("--icon", required=True)
	p.add_argument("--after")
	p.set_defaults(func=cmd_add_tile)
	p = gs.add_parser("remove-tile")
	p.add_argument("--title", required=True)
	p.set_defaults(func=cmd_remove_tile)
	p = gs.add_parser("set-icon")
	p.add_argument("--title", required=True)
	p.add_argument("--icon", required=True)
	p.set_defaults(func=cmd_set_icon)
	p = gs.add_parser("list")
	p.set_defaults(func=cmd_list)
