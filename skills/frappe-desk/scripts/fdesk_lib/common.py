"""Frozen foundation API. Other modules depend on these names; do not change signatures.

Byte format mirrors frappe.as_json / write_document_file (frappe v16):
json.dumps(indent=1, sort_keys=True, separators=(",", ": "), ensure_ascii=True) + "\n",
nulls dropped, child rows stripped of default fields.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

CHILD_STRIP = {
	"name", "parent", "parenttype", "parentfield", "idx", "doctype",
	"owner", "creation", "modified", "modified_by", "docstatus",
}
# kind -> (doctype, folder, module-rooted?)
KINDS = {
	"sidebar": ("Sidebar", "sidebar", None),  # module- or app-rooted
	"dock": ("Dock", "dock", False),  # always app-rooted
	"workspace": ("Workspace", "workspace", True),
	"number_card": ("Number Card", "number_card", True),
	"dashboard_chart": ("Dashboard Chart", "dashboard_chart", True),
}


class FdeskError(Exception):
	"""Usage or consistency error; CLI maps it to exit code 2."""


def scrub(name: str) -> str:
	"""frappe.scrub: lowercase, spaces and dashes to underscores."""
	return name.replace(" ", "_").replace("-", "_").lower()


def _strip(value, top: bool):
	if isinstance(value, dict):
		out = {}
		for k, v in value.items():
			if v is None:
				continue
			if not top and k in CHILD_STRIP:
				continue
			out[k] = _strip(v, False) if isinstance(v, (dict, list)) else v
		return out
	if isinstance(value, list):
		return [_strip(v, False) if isinstance(v, (dict, list)) else v for v in value]
	return value


def to_text(doc: dict) -> str:
	"""Serialise exactly like Frappe's exporter (top-level keys kept, child default fields dropped)."""
	cleaned = {}
	for k, v in doc.items():
		if v is None:
			continue
		if isinstance(v, list):
			cleaned[k] = [_strip(r, False) if isinstance(r, dict) else r for r in v]
		elif isinstance(v, dict):
			cleaned[k] = _strip(v, False)
		else:
			cleaned[k] = v
	return json.dumps(cleaned, indent=1, sort_keys=True, separators=(",", ": "), ensure_ascii=True) + "\n"


def _sorted_everywhere(v) -> bool:
	if isinstance(v, dict):
		keys = list(v)
		return keys == sorted(keys) and all(_sorted_everywhere(x) for x in v.values())
	if isinstance(v, list):
		return all(_sorted_everywhere(x) for x in v)
	return True


def detect_style(text: str) -> dict:
	"""Formatting of an existing file, so edits to hand-written files do not reformat them.
	Frappe-exported files are canonical (indent 1, sorted, ascii-escaped); hand-written ones often are not."""
	m = re.search(r"\n([ \t]+)\S", text)
	indent = 1
	if m:
		indent = "\t" if m.group(1).startswith("\t") else len(m.group(1))
	try:
		sort_keys = _sorted_everywhere(json.loads(text))
	except ValueError:
		sort_keys = True
	return {
		"indent": indent,
		"sort_keys": sort_keys,
		"ensure_ascii": not re.search(r"[^\x00-\x7f]", text),
		"newline": text.endswith("\n"),
	}


def render(doc: dict, style: dict) -> str:
	out = json.dumps(doc, indent=style["indent"], sort_keys=style["sort_keys"], separators=(",", ": "), ensure_ascii=style["ensure_ascii"])
	return out + ("\n" if style["newline"] else "")


def load(path) -> dict:
	return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, doc: dict) -> None:
	p = Path(path)
	p.parent.mkdir(parents=True, exist_ok=True)
	p.write_text(to_text(doc), encoding="utf-8", newline="\n")


def now_modified() -> str:
	return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


def bump_modified(doc: dict, base_modified: str | None = None) -> str:
	"""Set doc['modified'] strictly greater than both now and base_modified (frappe skips imports when file <= db)."""
	new = datetime.strptime(now_modified(), "%Y-%m-%d %H:%M:%S.%f")
	for prev in (base_modified, doc.get("modified")):
		if prev:
			try:
				p = datetime.strptime(str(prev)[:26].ljust(26, "0") if "." in str(prev) else str(prev) + ".000000", "%Y-%m-%d %H:%M:%S.%f")
			except ValueError:
				continue
			if p >= new:
				new = p + timedelta(seconds=1)
	doc["modified"] = new.strftime("%Y-%m-%d %H:%M:%S.%f")
	return doc["modified"]


@dataclass
class App:
	root: Path  # repo root (contains pyproject/setup + app package dir)
	name: str  # python package / app name
	package: Path  # root/name
	modules: list[str] = field(default_factory=list)

	@classmethod
	def find(cls, path=None) -> "App":
		"""Locate an app from any path inside it. App package = dir containing hooks.py and modules.txt."""
		start = Path(path or ".").resolve()
		for cand in [start, *start.parents]:
			pkgs = [cand] + sorted(d for d in cand.iterdir() if d.is_dir() and not d.name.startswith(".")) if cand.is_dir() else []
			for p in pkgs:
				if (p / "hooks.py").exists() and (p / "modules.txt").exists():
					mods = [m.strip() for m in (p / "modules.txt").read_text().splitlines() if m.strip()]
					return cls(root=p.parent if p != cand else p.parent, name=p.name, package=p, modules=mods)
		raise FdeskError(f"no Frappe app found at or above {start} (need hooks.py + modules.txt)")

	def module_dir(self, module: str) -> Path:
		if module not in self.modules:
			raise FdeskError(f"module {module!r} not in modules.txt ({self.modules})")
		return self.package / scrub(module)


def entity_path(app: App, kind: str, name: str, module: str | None = None, app_rooted: bool | None = None) -> Path:
	"""Path of an entity's JSON file. Folder and file are both scrub(name)."""
	if kind not in KINDS:
		raise FdeskError(f"unknown kind {kind!r}; expected one of {sorted(KINDS)}")
	_, folder, module_rooted = KINDS[kind]
	if kind == "sidebar":
		rooted_app = bool(app_rooted) or module is None
	else:
		rooted_app = not module_rooted
	base = app.package if rooted_app else app.module_dir(module or "")
	s = scrub(name)
	return base / folder / s / f"{s}.json"


def git_head_text(path) -> str | None:
	"""Content of the file at git HEAD, or None when untracked / no git."""
	p = Path(path).resolve()
	try:
		top = subprocess.run(["git", "-C", str(p.parent), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
		rel = p.relative_to(top)
		return subprocess.run(["git", "-C", top, "show", f"HEAD:{rel}"], capture_output=True, text=True, check=True).stdout
	except Exception:
		return None


def unified_diff(before: str, after: str, label: str) -> str:
	return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{label}", f"b/{label}"))


@dataclass
class Result:
	"""Every mutating command returns one. Changes are computed first; written only when write=True."""
	ok: bool = True
	changes: list[dict] = field(default_factory=list)  # {path, action: create|update|delete, diff}
	warnings: list[str] = field(default_factory=list)
	errors: list[str] = field(default_factory=list)
	info: dict = field(default_factory=dict)

	def to_json(self) -> str:
		return json.dumps({"ok": self.ok and not self.errors, "changes": self.changes, "warnings": self.warnings, "errors": self.errors, "info": self.info}, indent=1)


def plan_write(res: Result, path: Path, new_doc: dict, label: str | None = None, bump: bool = True) -> None:
	"""Record a create/update of `path` into res if bytes would change. Never bumps `modified` on a no-op."""
	path = Path(path)
	before = path.read_text(encoding="utf-8") if path.exists() else ""
	trial = json.loads(json.dumps(new_doc))
	style = detect_style(before) if before else None
	if before:
		base = json.loads(before).get("modified")
		if render({**trial, "modified": base}, style) == before:
			return  # no-op: leave modified alone
		if bump:
			bump_modified(trial, base)
	elif bump:
		bump_modified(trial)
	after = render(trial, style) if style else to_text(trial)
	res.changes.append({"path": str(path), "action": "update" if before else "create", "diff": unified_diff(before, after, label or path.name), "_text": after})


def apply(res: Result, write: bool) -> Result:
	"""Write planned changes when write=True; always strip internal text from the JSON-visible result."""
	for c in res.changes:
		if write:
			p = Path(c["path"])
			if c["action"] == "delete":
				if p.exists():
					p.unlink()
			else:
				p.parent.mkdir(parents=True, exist_ok=True)
				p.write_text(c["_text"], encoding="utf-8", newline="\n")
		c.pop("_text", None)
	res.info["written"] = bool(write)
	return res
