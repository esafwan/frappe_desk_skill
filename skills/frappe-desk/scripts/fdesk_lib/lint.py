"""`fdesk lint [--strict] [--only L01,L02] [--format text|json]`: static checks over every nav entity file in an app.

Rule IDs are frozen in tracks/frappe-desk-skill/WP-BRIEF.md (L01-L16). Each rule is `def lNN(app, ctx) -> list[Finding]`
registered in RULES. Facts are cited per rule from the Frappe v16 source (frappe/desk/doctype/*, frappe/model/sync.py).

Performance: one Ctx parses every file once; the catalog and icon union are scanned once per run (the two
backends re-stat the bench on every call, so they are memoised for the duration of the run).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import blocks, catalog, icons
from .common import App, FdeskError, Result, git_head_text, scrub

SEVERITIES = ("error", "warn", "info")
# sidebar/dock/workspace/number_card/dashboard_chart come from common.KINDS; dashboard is read for L08 only
SCAN_FOLDERS = {"sidebar": "sidebar", "dock": "dock", "workspace": "workspace", "number_card": "number_card", "dashboard_chart": "dashboard_chart", "dashboard": "dashboard"}
_PRUNE = {"node_modules", "public", "templates", "www", "__pycache__", "patches", "fixtures", "locale", "translations", "docs", ".git", ".fdesk-cache"}
# frappe/desk/doctype/workspace/workspace.json indicator_color options
INDICATOR_COLORS = {"green", "cyan", "blue", "orange", "yellow", "gray", "grey", "red", "pink", "darkgrey", "purple", "light-blue"}
LINK_KIND = {"DocType": "doctype", "Page": "page", "Report": "report", "Workspace": "workspace", "Dashboard": "dashboard"}


@dataclass
class Finding:
	rule: str
	severity: str
	path: str
	message: str
	fix_hint: str = ""

	def to_dict(self) -> dict:
		return {"rule": self.rule, "severity": self.severity, "path": self.path, "message": self.message, "fix_hint": self.fix_hint}


@dataclass
class Ent:
	kind: str
	path: Path
	rel: str
	doc: dict | None
	error: str = ""

	@property
	def name(self) -> str:
		return (self.doc or {}).get("name") or ""


class Ctx:
	"""Parsed files plus memoised catalog/icon lookups. `cat` and `icon` can be replaced (tests inject fakes)."""

	def __init__(self, app: App, cat=None, icon=None):
		self.app = app
		self.ents: dict[str, list[Ent]] = {k: [] for k in SCAN_FOLDERS}
		self.fixture_path = app.package / "fixtures" / "custom_html_block.json"
		self.fixtures: list[dict] | None = None
		self.fixture_error = ""
		self._cat_memo: dict = {}
		self._icon_memo: dict = {}
		self._cat = cat or (lambda kind, name: catalog.lookup(kind, name, app))
		self._icon = icon or (lambda name, context: icons.exists(name, context, app))
		self._scan()
		self._git()

	# ---- scanning
	def rel(self, p: Path) -> str:
		try:
			return str(Path(p).relative_to(self.app.root))
		except ValueError:
			return str(p)

	def _scan(self):
		for dirpath, dirs, files in os.walk(self.app.package):
			dirs[:] = sorted(d for d in dirs if d not in _PRUNE and not d.startswith("."))
			d = Path(dirpath)
			grand = d.parent.name
			kind = next((k for k, f in SCAN_FOLDERS.items() if f == grand), None)
			if not kind:
				continue
			for f in sorted(files):
				if not f.endswith(".json"):
					continue
				p = d / f
				try:
					doc = json.loads(p.read_text(encoding="utf-8"))
					if not isinstance(doc, dict):
						raise ValueError("top level is not an object")
					self.ents[kind].append(Ent(kind, p, self.rel(p), doc))
				except (OSError, ValueError) as e:
					self.ents[kind].append(Ent(kind, p, self.rel(p), None, str(e)))
		if self.fixture_path.exists():
			try:
				data = json.loads(self.fixture_path.read_text(encoding="utf-8"))
				self.fixtures = data if isinstance(data, list) else None
				if self.fixtures is None:
					self.fixture_error = "fixture file must be a JSON list of records"
			except (OSError, ValueError) as e:
				self.fixture_error = str(e)

	def _git(self):
		"""Changed / deleted tracked files vs HEAD, as absolute paths. One git call each, not one per file."""
		self.changed: set[Path] = set()
		self.deleted: set[Path] = set()
		for flt, bucket in ((None, self.changed), ("D", self.deleted)):
			cmd = ["git", "-C", str(self.app.package), "diff", "--name-only", "-z", "--relative", "HEAD"]
			if flt:
				cmd.insert(-1, f"--diff-filter={flt}")
			try:
				out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
			except Exception:
				return
			for rel in filter(None, out.split("\0")):
				bucket.add((self.app.package / rel).resolve())

	# ---- helpers
	def of(self, kind: str) -> list[Ent]:
		return [e for e in self.ents[kind] if e.doc is not None]

	def names(self, kind: str) -> set[str]:
		return {e.name for e in self.of(kind) if e.name}

	def cat(self, kind: str, name: str):
		key = (kind, name)
		if key not in self._cat_memo:
			self._cat_memo[key] = self._cat(kind, name)
		return self._cat_memo[key]

	def icon(self, name: str, context: str):
		key = (name, context)
		if key not in self._icon_memo:
			self._icon_memo[key] = self._icon(name, context)
		return self._icon_memo[key]

	def exists(self, kind: str, name: str):
		"""True/False/None. App files answer first; the catalog covers everything else."""
		if kind in self.ents and name in self.names(kind):
			return True
		if kind == "chart" and name in self.names("dashboard_chart"):
			return True
		return self.cat(kind, name)


def F(rule, sev, path, msg, hint="") -> Finding:
	return Finding(rule, sev, path, msg, hint)


def _rows(doc: dict, key: str) -> list[dict]:
	v = doc.get(key)
	return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []


def _s(v) -> str:
	return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def _flag(v) -> bool:
	return str(v) in ("1", "True", "true")


def _blocks(doc: dict):
	"""(blocks, error) from a workspace doc's content string."""
	raw = doc.get("content")
	if not raw:
		return [], ""
	try:
		out = json.loads(raw) if isinstance(raw, str) else list(raw)
	except ValueError as e:
		return [], f"content is not valid JSON: {e}"
	if not isinstance(out, list):
		return [], "content must be a JSON list of blocks"
	return [b for b in out if isinstance(b, dict)], ""


def _parse_dt(v):
	try:
		return datetime.strptime(str(v)[:26].ljust(26, "0") if "." in str(v) else str(v) + ".000000", "%Y-%m-%d %H:%M:%S.%f")
	except ValueError:
		return None


# ---------------------------------------------------------------- rules
def l01(app, ctx):
	"""frappe/model/sync.py remove_orphan_entities: standard rows without a file at scrub(name) are deleted on migrate;
	sidebar.py validate_title_is_routable / validate_title_is_its_own restrict titles."""
	out = []
	for kind in ("sidebar", "dock", "workspace", "number_card", "dashboard_chart"):
		for e in ctx.ents[kind]:
			if e.doc is None:
				out.append(F("L01", "error", e.rel, f"invalid JSON: {e.error}", "fix the syntax; re-create with the fdesk command if unsure"))
				continue
			name = e.name
			if not name:
				out.append(F("L01", "error", e.rel, "document has no 'name'"))
				continue
			want = scrub(name)
			if e.path.parent.name != want or e.path.stem != want:
				out.append(F("L01", "error", e.rel, f"path must be {kind_folder(kind)}/{want}/{want}.json for name {name!r} (found folder {e.path.parent.name!r}, file {e.path.name!r})", f"git mv the folder and file to {want}"))
			if kind == "sidebar":
				title = _s(e.doc.get("title"))
				if title and title != name:
					out.append(F("L01", "warn", e.rel, f"Sidebar autoname is the title: name {name!r} != title {title!r}", "make name equal title"))
				if re.search(r"[/?#%\\]", title):
					out.append(F("L01", "error", e.rel, f"Sidebar title {title!r} contains one of / ? # % \\ (not routable)", "choose another title"))
				if title in app.modules and title != e.doc.get("module"):
					out.append(F("L01", "error", e.rel, f"Sidebar title {title!r} equals another module's name (sidebar.py validate_title_is_its_own)", "choose another title"))
	return out


def kind_folder(kind: str) -> str:
	return SCAN_FOLDERS[kind]


def l02(app, ctx):
	"""frappe/modules/import_file.py skips an import when file.modified <= db.modified."""
	out = []
	for kind in ("sidebar", "dock", "workspace", "number_card", "dashboard_chart"):
		for e in ctx.of(kind):
			if e.path.resolve() not in ctx.changed:
				continue
			head = git_head_text(e.path)
			if head is None:
				continue
			try:
				old = json.loads(head)
			except ValueError:
				continue
			strip = lambda d: {k: v for k, v in d.items() if k != "modified"}
			if strip(old) == strip(e.doc):
				continue
			new_m, old_m = e.doc.get("modified"), old.get("modified")
			a, b = _parse_dt(new_m), _parse_dt(old_m)
			stale = (a is not None and b is not None and a <= b) or (a is None and b is None and str(new_m) <= str(old_m)) or not new_m
			if stale:
				out.append(F("L02", "error", e.rel, f"content changed but 'modified' ({new_m}) is not newer than git HEAD ({old_m}); migrate will skip it", "re-run the fdesk command with --write (it bumps modified), or set a newer 'modified'"))
	return out


def l03(app, ctx):
	"""frontend sidebar_item.js: a Section Break with a link navigates instead of expanding; each section starts with an Overview."""
	out = []
	for e in ctx.of("sidebar"):
		items = _rows(e.doc, "items")
		for i, it in enumerate(items):
			if it.get("type") != "Section Break":
				continue
			label = it.get("label")
			bad = [k for k in ("link_to", "url", "route_options", "filters") if it.get(k)]
			if bad:
				out.append(F("L03", "error", e.rel, f"Section Break {label!r} carries {', '.join(bad)}; a section must only expand", f"fdesk sidebar remove-section {e.name!r} --label {label!r} --force, then re-add with `add-section`"))
			kids = []
			for nxt in items[i + 1:]:
				if nxt.get("type") == "Section Break" or not nxt.get("child"):
					break
				kids.append(nxt)
			first = kids[0] if kids else None
			if kids and all(k.get("link_type") == "URL" for k in kids):
				continue  # launcher section (URL links): an Overview workspace is not expected
			if not first or first.get("type") != "Link" or first.get("label") != "Overview":
				out.append(F("L03", "warn", e.rel, f"Section {label!r} is not followed by an Overview link child", f"fdesk sidebar add-item {e.name!r} --section {label!r} --link-type Workspace --to <Workspace> --label Overview"))
	return out


def l04(app, ctx):
	"""frappe/desk/doctype/sidebar/sidebar.py LINKED_IDENTITY_FIELDS: same (type, link_type, link_to, url, filters) collapses; unlinked rows hash type+label."""
	out = []
	for e in ctx.of("sidebar"):
		seen = {}
		for it in _rows(e.doc, "items"):
			if it.get("link_to") or it.get("url"):
				key = tuple(_s(it.get(k)) for k in ("type", "link_type", "link_to", "url", "filters"))
			else:
				key = ("unlinked", _s(it.get("type")), _s(it.get("label")))
			if key in seen:
				what = f"{it.get('link_type')} {it.get('link_to') or it.get('url')}" if key[0] != "unlinked" else f"{key[1]} {key[2]!r}"
				out.append(F("L04", "error", e.rel, f"duplicate item ({what}): {it.get('label')!r} collapses into {seen[key]!r}", f"fdesk sidebar remove-item {e.name!r} --label {it.get('label')!r}, or give it a distinct label (spacers) / filters"))
			else:
				seen[key] = it.get("label")
	return out


def _icon_check(ctx, rule, rel, entries, out):
	"""entries: (what, icon, context)."""
	unknown = 0
	for what, icon, context in entries:
		if not icon:
			continue
		r = ctx.icon(icon, context)
		if r is None:
			unknown += 1
		elif r is False:
			hint = f"fdesk icons check {icon} --context {context}; fdesk icons search <keyword>"
			note = " (-duotone icons are valid on Dock tiles only)" if str(icon).endswith("-duotone") and context != "dock" else ""
			out.append(F("L05", "error", rel, f"{what}: unknown icon {icon!r}{note}", hint))
	if unknown:
		out.append(F("L05", "info", rel, f"{unknown} icon(s) cannot be verified (no icon sprites found; bench missing?)", "run inside a bench so the sprite union can be read"))


def l05(app, ctx):
	"""frappe/public/js/frappe/utils/utils.js: an unknown icon draws nothing. icons.exists(): -duotone is dock-only."""
	out = []
	for e in ctx.of("sidebar"):
		ent = [("header_icon", e.doc.get("header_icon"), "sidebar")]
		if not e.doc.get("header_icon"):
			out.append(F("L05", "warn", e.rel, "Sidebar has no header_icon (rail shows a letter/hammer tile)", "set header_icon"))
		ent += [(f"item {it.get('label')!r}", it.get("icon"), "sidebar") for it in _rows(e.doc, "items")]
		_icon_check(ctx, "L05", e.rel, ent, out)
	for e in ctx.of("dock"):
		ent = []
		for it in _rows(e.doc, "items"):
			if not it.get("icon"):
				out.append(F("L05", "warn", e.rel, f"Dock tile {it.get('title')!r} has no icon (letter tile)", f"fdesk dock set-icon --title {it.get('title')!r} --icon <name>"))
			ent.append((f"tile {it.get('title')!r}", it.get("icon"), "dock"))
		_icon_check(ctx, "L05", e.rel, ent, out)
	for e in ctx.of("workspace"):
		_icon_check(ctx, "L05", e.rel, [("workspace icon", e.doc.get("icon"), "workspace")], out)
	return out


def _check_target(ctx, rel, rule, what, link_type, to, out, unknown):
	kind = LINK_KIND.get(link_type)
	if not kind or not to:
		return
	r = ctx.exists(kind, to)
	if r is None:
		unknown.append(1)
	elif r is False:
		out.append(F(rule, "error", rel, f"{what}: {link_type} {to!r} does not exist", f"fdesk catalog search {to!r} --kind {kind}"))


def l06(app, ctx):
	"""A link to a missing DocType/Report/Page/Workspace/Dashboard opens 'not found'. Workspaces resolve against the app and the catalog."""
	out = []
	for e in ctx.of("sidebar"):
		unk = []
		for it in _rows(e.doc, "items"):
			if it.get("type", "Link") == "Link":
				_check_target(ctx, e.rel, "L06", f"item {it.get('label')!r}", it.get("link_type"), it.get("link_to"), out, unk)
		if unk:
			out.append(F("L06", "info", e.rel, f"{len(unk)} link target(s) cannot be verified (no bench/frappe checkout for the catalog)", "run inside a bench"))
	for e in ctx.of("workspace"):
		unk = []
		d = e.doc
		for r in _rows(d, "shortcuts"):
			if r.get("type") != "URL":
				_check_target(ctx, e.rel, "L06", f"shortcut {r.get('label')!r}", r.get("type"), r.get("link_to"), out, unk)
		for r in _rows(d, "links"):
			if r.get("type") == "Link":
				_check_target(ctx, e.rel, "L06", f"link {r.get('label')!r}", r.get("link_type"), r.get("link_to"), out, unk)
		if d.get("type") == "Link":
			_check_target(ctx, e.rel, "L06", "workspace link", d.get("link_type"), d.get("link_to"), out, unk)
		if unk:
			out.append(F("L06", "info", e.rel, f"{len(unk)} link target(s) cannot be verified (no bench/frappe checkout for the catalog)", "run inside a bench"))
	return out


# content block type -> (data key, child table, child fields that can match the block's name)
# frappe/public/js/frappe/widgets/*.js and desk/desktop.py (onboarding_name) / workspace_*.json
BLOCK_MAP = {
	"shortcut": ("shortcut_name", "shortcuts", ("label",)),
	"chart": ("chart_name", "charts", ("label", "chart_name")),
	"number_card": ("number_card_name", "number_cards", ("label", "number_card_name")),
	"quick_list": ("quick_list_name", "quick_lists", ("label",)),
	"custom_block": ("custom_block_name", "custom_blocks", ("label",)),  # views/workspace/blocks/block.js make(): row.label == block name
}
PLAIN_TYPES = {"header", "paragraph", "spacer"}


def l07(app, ctx):
	out = []
	for e in ctx.of("workspace"):
		d = e.doc
		blks, err = _blocks(d)
		if err:
			out.append(F("L07", "error", e.rel, err))
			continue
		ids, used = {}, {k: set() for k in list(BLOCK_MAP) + ["card"]}
		cards = {r.get("label") for r in _rows(d, "links") if r.get("type") == "Card Break"}
		for b in blks:
			bid, bt, data = b.get("id"), b.get("type"), b.get("data") or {}
			if not bid:
				out.append(F("L07", "error", e.rel, f"content block of type {bt!r} has no id"))
			elif bid in ids:
				out.append(F("L07", "error", e.rel, f"duplicate content block id {bid!r}", "give each block a unique id"))
			ids[bid] = 1
			if bt in PLAIN_TYPES:
				continue
			if bt == "card":
				n = data.get("card_name")
				used["card"].add(n)
				if n not in cards:
					out.append(F("L07", "error", e.rel, f"card block {bid!r} names {n!r} but no Card Break link has that label", f"fdesk workspace add-link-card {e.name!r} --label {n!r}"))
			elif bt in BLOCK_MAP:
				key, table, fields = BLOCK_MAP[bt]
				n = data.get(key)
				used[bt].add(n)
				rows = _rows(d, table)
				if not any(n and n in [r.get(f) for f in fields] for r in rows):
					out.append(F("L07", "error", e.rel, f"{bt} block {bid!r} names {n!r} but {table} has no row with that {'/'.join(fields)}", _l07_hint(bt, e.name, n)))
			elif bt == "onboarding":
				n = data.get("onboarding_name")
				if not d.get("module_onboarding") or d.get("module_onboarding") != n:
					out.append(F("L07", "error", e.rel, f"onboarding block {bid!r} names {n!r} but module_onboarding is {d.get('module_onboarding')!r}", "set module_onboarding to the same Module Onboarding name"))
			else:
				out.append(F("L07", "warn", e.rel, f"unknown content block type {bt!r} (block {bid!r})", "known: header, paragraph, spacer, shortcut, card, chart, number_card, quick_list, custom_block, onboarding"))
		for bt, (key, table, fields) in BLOCK_MAP.items():
			for r in _rows(d, table):
				ident = next((r.get(f) for f in fields if r.get(f)), None)
				if ident and not any(r.get(f) in used[bt] for f in fields):
					out.append(F("L07", "warn", e.rel, f"orphan {table} row {ident!r}: no {bt} content block uses it", f"fdesk workspace add-{bt.replace('_', '-')} {e.name!r} ... or remove the row"))
		for c in cards - used["card"]:
			out.append(F("L07", "warn", e.rel, f"orphan Card Break {c!r}: no card content block uses it", f"fdesk workspace add-link-card {e.name!r} --label {c!r} re-adds the block"))
	return out


def _l07_hint(bt, ws, n):
	return {
		"shortcut": f"fdesk workspace add-shortcut {ws!r} --type DocType --to <X> --label {n!r}",
		"chart": f"fdesk workspace add-chart {ws!r} --chart {n!r}",
		"number_card": f"fdesk workspace add-number-card {ws!r} --card {n!r}",
		"quick_list": f"fdesk workspace add-quick-list {ws!r} --doctype <X> --label {n!r}",
		"custom_block": f"fdesk block new --kind <kind> --name {n!r} --workspace {ws!r}",
	}.get(bt, "")


def l08(app, ctx):
	"""Number Card / Dashboard Chart are synced by sync_dashboards and are NOT in ORPHANABLE_ENTITIES (frappe/model/sync.py):
	a deleted file leaves the DB row, so removal needs a patch."""
	out = []
	for kind, label in (("number_card", "Number Card"), ("dashboard_chart", "Dashboard Chart")):
		folder = SCAN_FOLDERS[kind]
		for p in sorted(ctx.deleted):
			if p.parent.parent.name == folder and p.suffix == ".json":
				out.append(F("L08", "warn", ctx.rel(p), f"{label} file {p.stem!r} existed at git HEAD but is deleted: reaper does not remove Number Card/Dashboard Chart rows: add a patch", "add a patch calling frappe.delete_doc and update workspaces that referenced it"))

	def check(rel, what, kind_cat, name, unk):
		if not name:
			return
		r = ctx.exists(kind_cat, name)
		if r is None:
			unk.append(1)
		elif r is False:
			out.append(F("L08", "error", rel, f"{what} {name!r} has no file in this app and is not in the catalog", "fdesk charts chart new / number-card new (or fix the name)"))

	for e in ctx.of("workspace"):
		unk = []
		for r in _rows(e.doc, "charts"):
			check(e.rel, "chart", "chart", r.get("chart_name"), unk)
		for r in _rows(e.doc, "number_cards"):
			check(e.rel, "number card", "number_card", r.get("number_card_name"), unk)
		blks, _ = _blocks(e.doc)
		labels = {r.get(f): r for t in ("charts", "number_cards") for r in _rows(e.doc, t) for f in ("label",) if r.get(f)}
		for b in blks:
			data = b.get("data") or {}
			if b.get("type") == "chart" and data.get("chart_name") and data["chart_name"] not in labels and not any(r.get("chart_name") == data["chart_name"] for r in _rows(e.doc, "charts")):
				check(e.rel, "chart", "chart", data["chart_name"], unk)
			if b.get("type") == "number_card" and data.get("number_card_name") and data["number_card_name"] not in labels and not any(r.get("number_card_name") == data["number_card_name"] for r in _rows(e.doc, "number_cards")):
				check(e.rel, "number card", "number_card", data["number_card_name"], unk)
		if unk:
			out.append(F("L08", "info", e.rel, f"{len(unk)} chart/number card reference(s) cannot be verified (no catalog)", "run inside a bench"))
	for e in ctx.of("dashboard"):
		unk = []
		for r in _rows(e.doc, "charts"):
			check(e.rel, "chart", "chart", r.get("chart"), unk)
		for r in _rows(e.doc, "cards"):
			check(e.rel, "number card", "number_card", r.get("card"), unk)
		if unk:
			out.append(F("L08", "info", e.rel, f"{len(unk)} dashboard reference(s) cannot be verified (no catalog)", "run inside a bench"))
	return out


def l09(app, ctx):
	"""frappe/desk/doctype/workspace/workspace.json: reqd label,title,module,type; Link needs link_type+link_to, URL needs external_link;
	frappe/model/sync.py reaps standard=1 public=1 workspaces without a file."""
	out = []
	for e in ctx.of("workspace"):
		d, rel = e.doc, e.rel
		for k in ("label", "title", "module", "type"):
			if not _s(d.get(k)):
				out.append(F("L09", "error", rel, f"required header key {k!r} is missing", f"set {k}"))
		t = d.get("type")
		if t and t not in ("Workspace", "Link", "URL"):
			out.append(F("L09", "error", rel, f"type {t!r} must be Workspace, Link or URL"))
		if t == "Link":
			if d.get("link_type") not in ("DocType", "Page", "Report") or not d.get("link_to"):
				out.append(F("L09", "error", rel, "type Link needs link_type (DocType|Page|Report) and link_to"))
		if t == "URL" and not d.get("external_link"):
			out.append(F("L09", "error", rel, "type URL needs external_link"))
		if not _flag(d.get("standard")):
			out.append(F("L09", "error", rel, "standard must be 1 for an app-shipped workspace", "set \"standard\": 1"))
		if not _flag(d.get("public")):
			out.append(F("L09", "warn", rel, "public is not 1; a non-public workspace is invisible to other users and ignored by the reaper", "set \"public\": 1"))
		pp = d.get("parent_page")
		if pp:
			r = ctx.exists("workspace", pp)
			if r is False:
				out.append(F("L09", "error", rel, f"parent_page {pp!r} does not exist", f"fdesk catalog search {pp!r} --kind workspace"))
		c = d.get("indicator_color")
		if c and c not in INDICATOR_COLORS:
			out.append(F("L09", "error", rel, f"indicator_color {c!r} not in {sorted(INDICATOR_COLORS)}"))
		if str(d.get("is_hidden", 0)) not in ("0", "1", "True", "False"):
			out.append(F("L09", "error", rel, f"is_hidden must be 0 or 1, got {d.get('is_hidden')!r}"))
		for r in _rows(d, "roles"):
			if not _s(r.get("role")):
				out.append(F("L09", "error", rel, f"roles row without a role: {r}"))
		mod = _s(d.get("module"))
		if mod and e.path.parent.parent.parent.name != scrub(mod) and e.path.parent.parent.parent != app.package:
			out.append(F("L09", "warn", rel, f"module {mod!r} differs from the containing module folder {e.path.parent.parent.parent.name!r}"))
	return out


def l10(app, ctx):
	"""sidebar_item.json / dock_item.json: a URL row opens `url`; `link_to` is ignored for link_type URL."""
	out = []
	for e in ctx.of("sidebar"):
		for it in _rows(e.doc, "items"):
			if it.get("link_type") == "URL":
				if it.get("link_to"):
					out.append(F("L10", "error", e.rel, f"URL item {it.get('label')!r} sets link_to; use url", f"fdesk sidebar remove-item {e.name!r} --label {it.get('label')!r}; add-item --link-type URL --url <U>"))
				if not it.get("url"):
					out.append(F("L10", "error", e.rel, f"URL item {it.get('label')!r} has no url"))
			elif it.get("url") and it.get("type", "Link") == "Link":
				out.append(F("L10", "warn", e.rel, f"item {it.get('label')!r} has url but link_type is {it.get('link_type')!r}; url is ignored"))
	for e in ctx.of("dock"):
		for it in _rows(e.doc, "items"):
			if it.get("link_type") == "URL":
				if it.get("link_to"):
					out.append(F("L10", "error", e.rel, f"URL tile {it.get('title')!r} sets link_to; use url", f"fdesk dock remove-tile --title {it.get('title')!r}; add-tile --link-type URL --url <U>"))
				if not it.get("url"):
					out.append(F("L10", "error", e.rel, f"URL tile {it.get('title')!r} has no url"))
			elif not it.get("link_to"):
				out.append(F("L10", "error", e.rel, f"{it.get('link_type')} tile {it.get('title')!r} has no link_to"))
	return out


def l11(app, ctx):
	"""sidebar.py: is_default_module claims a target for one sidebar; two claims make the module open the wrong sidebar."""
	out, claims = [], {}
	for e in ctx.of("sidebar"):
		for it in _rows(e.doc, "items"):
			if not _flag(it.get("is_default_module")):
				continue
			if it.get("link_type") == "URL":
				out.append(F("L11", "error", e.rel, f"is_default_module on URL item {it.get('label')!r}; only entities can be a module default", f"remove is_default_module from {it.get('label')!r}"))
				continue
			claims.setdefault((it.get("link_type"), it.get("link_to")), []).append((e, it))
	for (lt, to), lst in claims.items():
		if len(lst) > 1:
			where = ", ".join(f"{e.name}:{it.get('label')}" for e, it in lst)
			out.append(F("L11", "error", lst[0][0].rel, f"{len(lst)} items claim is_default_module for {lt} {to!r}: {where}", "keep it on one item"))
	return out


_FIXTURE_ERR = re.compile(r"external|private|roles|doctype|name is empty|document\.querySelector|never uses root_element", re.I)


def l12(app, ctx):
	"""Custom HTML Block ships as a fixture (frappe/utils/fixtures.py). html goes through frappe.dom.remove_script_and_style, so
	<script> inside html never runs; the script field runs with root_element in a shadow root."""
	out = []
	rel = ctx.rel(ctx.fixture_path)
	if ctx.fixture_error:
		return [F("L12", "error", rel, f"fixture file unreadable: {ctx.fixture_error}")]
	hooks = app.package / "hooks.py"
	htxt = hooks.read_text(encoding="utf-8", errors="replace") if hooks.exists() else ""
	if ctx.fixtures is not None and "Custom HTML Block" not in htxt:
		out.append(F("L12", "warn", rel, "hooks.py has no 'fixtures' entry for Custom HTML Block; the file is never imported on migrate", "fdesk block new ... (ensures the hook)"))
	for rec in ctx.fixtures or []:
		if not isinstance(rec, dict):
			out.append(F("L12", "error", rel, f"fixture record is not an object: {rec!r}"))
			continue
		name = rec.get("name")
		for issue in blocks.lint_block(rec):
			out.append(F("L12", "error" if _FIXTURE_ERR.search(issue) else "warn", rel, f"{name}: {issue}", f"fdesk block list; edit {rel}"))
		if re.search(r"<\s*script\b", rec.get("html") or "", re.I):
			out.append(F("L12", "warn", rel, f"{name}: scripts inside the html field are stripped by frappe.dom.remove_script_and_style; use the script field", "move the code to the 'script' field"))
		roles = [r.get("role") for r in rec.get("roles") or [] if isinstance(r, dict) and r.get("role")]
		if roles:
			out.append(F("L12", "info", rel, f"{name}: visible only to roles {roles}; users without them (including the sidebar audience) will not see the block", "confirm the sidebar's audience has one of these roles"))
	names = {r.get("name") for r in ctx.fixtures or [] if isinstance(r, dict)}
	for e in ctx.of("workspace"):
		for r in _rows(e.doc, "custom_blocks"):
			n = r.get("custom_block_name")
			if n and n not in names and ctx.cat("custom_block", n) is False:
				out.append(F("L12", "error", e.rel, f"custom_blocks row {n!r} is not in fixtures/custom_html_block.json and not in the catalog", "fdesk block new --name ..."))
	return out


def l13(app, ctx):
	"""frappe/desk/doctype/dock/dock.py get_app_dock: the rail shows an app only if it ships a standard Dock whose tiles resolve."""
	out, rel0 = [], ctx.rel(app.package)
	docks = [e for e in ctx.of("dock") if _flag(e.doc.get("standard"))]
	if not docks:
		out.append(F("L13", "error", rel0, "app has no standard Dock (dock/<app>/<app>.json with standard=1); the rail has no tile", "fdesk dock add-tile --link-type Sidebar --to <Sidebar> --title <T> --icon <I> --write"))
	elif len(docks) > 1:
		out.append(F("L13", "warn", docks[1].rel, f"{len(docks)} standard Docks in one app; only one is expected"))
	for e in ctx.of("dock"):
		unk = 0
		for it in _rows(e.doc, "items"):
			lt, to = it.get("link_type"), it.get("link_to")
			if lt not in ("Sidebar", "Workspace") or not to:
				continue
			r = ctx.exists(lt.lower(), to)
			if r is None:
				unk += 1
			elif r is False:
				out.append(F("L13", "error", e.rel, f"tile {it.get('title')!r} targets missing {lt} {to!r}", f"fdesk dock remove-tile --title {it.get('title')!r}, or create the {lt.lower()}"))
		if unk:
			out.append(F("L13", "info", e.rel, f"{unk} dock target(s) cannot be verified (no catalog)", "run inside a bench"))
	for e in ctx.of("sidebar"):
		if e.doc.get("module") and not any(it.get("link_type") == "Workspace" for it in _rows(e.doc, "items")):
			out.append(F("L13", "warn", e.rel, "module Sidebar has no Workspace landing item", f"fdesk sidebar add-item {e.name!r} --section <S> --link-type Workspace --to <Workspace> --label Overview"))
	return out


def l14(app, ctx):
	"""workspace_link.json / workspace_shortcut.json required fields; desk/doctype/workspace/workspace.py uses link_count to slice the links under a Card Break."""
	out = []
	for e in ctx.of("workspace"):
		d, rel = e.doc, e.rel
		links = _rows(d, "links")
		cur = None
		counts = {}
		for i, r in enumerate(links):
			t = r.get("type")
			if t not in ("Link", "Card Break"):
				out.append(F("L14", "error", rel, f"links[{i}] type {t!r} must be Link or Card Break"))
			if not _s(r.get("label")):
				out.append(F("L14", "error", rel, f"links[{i}] has no label"))
			if t == "Card Break":
				cur = i
				counts[i] = 0
			elif t == "Link":
				if cur is None:
					out.append(F("L14", "warn", rel, f"link {r.get('label')!r} appears before any Card Break"))
				else:
					counts[cur] += 1
				if r.get("link_type") not in ("DocType", "Page", "Report") or not r.get("link_to"):
					out.append(F("L14", "error", rel, f"link {r.get('label')!r} needs link_type (DocType|Page|Report) and link_to"))
		for i, n in counts.items():
			have = int(links[i].get("link_count") or 0)
			if have != n:
				out.append(F("L14", "error", rel, f"Card Break {links[i].get('label')!r} link_count {have} != {n} Link rows under it", "fdesk workspace add-link/remove-link recompute it, or set link_count"))
		for i, r in enumerate(_rows(d, "shortcuts")):
			if r.get("type") not in ("DocType", "Report", "Page", "Dashboard", "URL") or not _s(r.get("label")):
				out.append(F("L14", "error", rel, f"shortcuts[{i}] needs a valid type and a label ({r.get('label')!r})"))
			elif r.get("type") == "URL" and not r.get("url"):
				out.append(F("L14", "error", rel, f"URL shortcut {r.get('label')!r} needs url", f"fdesk workspace remove-shortcut {e.name!r} --label {r.get('label')!r}; add-shortcut --type URL --url <U>"))
			elif r.get("type") != "URL" and not r.get("link_to"):
				out.append(F("L14", "error", rel, f"{r.get('type')} shortcut {r.get('label')!r} needs link_to"))
		for i, r in enumerate(_rows(d, "charts")):
			if not r.get("chart_name"):
				out.append(F("L14", "error", rel, f"charts[{i}] has no chart_name"))
		for i, r in enumerate(_rows(d, "number_cards")):
			if not r.get("number_card_name"):
				out.append(F("L14", "error", rel, f"number_cards[{i}] has no number_card_name"))
		for i, r in enumerate(_rows(d, "quick_lists")):
			if not r.get("document_type") or not _s(r.get("label")):
				out.append(F("L14", "error", rel, f"quick_lists[{i}] needs document_type and label"))
	return out


def l15(app, ctx):
	"""number_card.json / dashboard_chart.json mandatory_depends_on; both sync via sync_dashboards only when is_standard=1 with a module."""
	out = []

	def need(rel, what, d, keys):
		for k in keys:
			if not _s(d.get(k)):
				out.append(F("L15", "error", rel, f"{what}: {k!r} is required", f"fdesk charts list; set {k}"))

	for e in ctx.of("number_card"):
		d, rel = e.doc, e.rel
		if not _flag(d.get("is_standard")):
			out.append(F("L15", "error", rel, "is_standard must be 1 (otherwise sync_dashboards ignores the file)"))
		need(rel, "Number Card", d, ["module", "label"])
		t = d.get("type") or "Document Type"
		if t == "Document Type":
			need(rel, "Number Card (Document Type)", d, ["document_type", "function"])
			if d.get("function") and d.get("function") != "Count":
				need(rel, "Number Card (function != Count)", d, ["aggregate_function_based_on"])
		elif t == "Report":
			need(rel, "Number Card (Report)", d, ["report_name", "report_field", "report_function"])
		elif t == "Custom":
			need(rel, "Number Card (Custom)", d, ["method"])
		else:
			out.append(F("L15", "error", rel, f"type {t!r} must be Document Type, Report or Custom"))
	for e in ctx.of("dashboard_chart"):
		d, rel = e.doc, e.rel
		if not _flag(d.get("is_standard")):
			out.append(F("L15", "error", rel, "is_standard must be 1 (otherwise sync_dashboards ignores the file)"))
		need(rel, "Dashboard Chart", d, ["module", "chart_name"])
		if d.get("filters_json") is None:
			out.append(F("L15", "error", rel, "'filters_json' is required (use \"[]\")", "set filters_json to \"[]\""))
		ct = d.get("chart_type")
		if ct in ("Count", "Sum", "Average"):
			need(rel, f"Dashboard Chart ({ct})", d, ["document_type", "based_on"] + (["value_based_on"] if ct != "Count" else []))
		elif ct == "Group By":
			need(rel, "Dashboard Chart (Group By)", d, ["document_type", "group_by_based_on"])
			if d.get("group_by_type") in ("Sum", "Average"):
				need(rel, "Dashboard Chart (Group By Sum/Average)", d, ["aggregate_function_based_on"])
		elif ct == "Report":
			need(rel, "Dashboard Chart (Report)", d, ["report_name"])
		elif ct == "Custom":
			need(rel, "Dashboard Chart (Custom)", d, ["source"])
		else:
			out.append(F("L15", "error", rel, f"chart_type {ct!r} must be Count, Sum, Average, Group By, Report or Custom"))
	return out


def l16(app, ctx):
	"""frappe/desk/doctype/custom_sidebar: site/user rows override or hide app items, so file changes can appear to do nothing."""
	if not ctx.of("sidebar"):
		return []
	return [F("L16", "info", ctx.rel(app.package), "a site's Custom Sidebar rows can mask these files (label/icon overrides, hidden items); lint cannot see site data", "run `fdesk verify` against the site to list overriding rows")]


RULES = {f"L{i:02d}": globals()[f"l{i:02d}"] for i in range(1, 17)}


# ---------------------------------------------------------------- engine / CLI
@contextlib.contextmanager
def _memoised_backends(app):
	"""catalog._ensure and icons.load_union re-scan the bench on every call; scan once per run."""
	oe, ol = catalog._ensure, icons.load_union
	cache = {}

	def ensure(a, force=False):
		if "c" not in cache:
			cache["c"] = oe(a, force)
		return cache["c"]

	def union(a=None):
		if "u" not in cache:
			cache["u"] = ol(a)
		return cache["u"]

	catalog._ensure, icons.load_union = ensure, union
	try:
		yield
	finally:
		catalog._ensure, icons.load_union = oe, ol


def run_lint(app: App, only=None, ctx: Ctx | None = None) -> list[Finding]:
	ids = sorted(only) if only else sorted(RULES)
	unknown = [i for i in ids if i not in RULES]
	if unknown:
		raise FdeskError(f"unknown lint rule(s) {unknown}; known: {sorted(RULES)}")
	findings: list[Finding] = []
	with _memoised_backends(app):
		ctx = ctx or Ctx(app)
		for i in ids:
			findings.extend(RULES[i](app, ctx))
	order = {s: n for n, s in enumerate(SEVERITIES)}
	findings.sort(key=lambda f: (order[f.severity], f.rule, f.path, f.message))
	return findings


def cmd_lint(args) -> Result:
	app = App.find(args.app)
	only = [x.strip().upper() for x in args.only.split(",") if x.strip()] if args.only else None
	findings = run_lint(app, only)
	if args.strict:
		# a gate that cannot verify icons/targets must not pass silently (no bench found -> info)
		for f in findings:
			if f.severity == "info" and "cannot be verified" in f.message:
				f.severity = "warn"
				f.message += " (--strict: run with the app inside a bench, or set FDESK_BENCH)"
		findings.sort(key=lambda f: ({s: n for n, s in enumerate(SEVERITIES)}[f.severity], f.rule, f.path, f.message))
	counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
	failed = counts["error"] > 0 or (args.strict and counts["warn"] > 0)
	res = Result(ok=not failed)
	res.info = {"findings": [f.to_dict() for f in findings], "counts": counts, "strict": bool(args.strict)}
	as_json = getattr(args, "json", False)
	if as_json:
		res.errors = [f"{f.rule} {f.path}: {f.message}" for f in findings if f.severity == "error"]
		res.warnings = [f"{f.rule} {f.path}: {f.message}" for f in findings if f.severity == "warn"]
		if args.strict and counts["warn"] and not res.errors:
			res.errors = ["--strict: warnings count as errors"]
		return res
	if args.format == "json":
		print(json.dumps({"ok": not failed, "counts": counts, "findings": res.info["findings"]}, indent=1))
	else:
		for f in findings:
			print(f"{f.severity:<5} {f.rule} {f.path}: {f.message}" + (f"\n      fix: {f.fix_hint}" if f.fix_hint else ""))
		print(f"{counts['error']} error(s), {counts['warn']} warning(s), {counts['info']} info")
	res.info["printed"] = True
	return res


def register(sub):
	p = sub.add_parser("lint", help="static checks over every nav entity file (L01-L16)")
	p.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
	p.add_argument("--only", default=None, help="comma list of rule ids, e.g. L01,L02")
	p.add_argument("--format", choices=["text", "json"], default="text")
	p.set_defaults(func=cmd_lint, cmd="list")  # cmd=list: read-only, suppresses 'no changes'
