import json
import subprocess
import sys
from pathlib import Path

import pytest

from fdesk_lib import lint
from fdesk_lib.common import to_text

ROOT = Path(__file__).resolve().parent.parent
FDESK = ROOT / "skills" / "frappe-desk" / "scripts" / "fdesk"


def P(app, rel):
	return app.package / rel


SIDEBAR = "sample_mod/sidebar/sample_app/sample_app.json"
DOCK = "dock/sample_app/sample_app.json"
HOME = "sample_mod/workspace/sample_home/sample_home.json"
OPS = "sample_ops/workspace/sample_ops/sample_ops.json"


def edit(app, rel, fn):
	p = P(app, rel)
	d = json.loads(p.read_text())
	r = fn(d)
	p.write_text(to_text(d if r is None else r))


def run(app, only=None, cat=None, icon=None):
	ctx = lint.Ctx(app, cat=cat or (lambda k, n: True), icon=icon or (lambda n, c: True))
	return lint.run_lint(app, only, ctx=ctx)


def rules(findings, rule, sev=None):
	return [f for f in findings if f.rule == rule and (sev is None or f.severity == sev)]


def git(app, *args):
	subprocess.run(["git", "-C", str(app.root), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)


# ---- clean app
def test_clean_sample_app_passes(app):
	fs = run(app)
	assert [f for f in fs if f.severity in ("error", "warn")] == []


def test_every_rule_registered():
	assert sorted(lint.RULES) == [f"L{i:02d}" for i in range(1, 17)]


def test_unknown_rule_rejected(app):
	from fdesk_lib.common import FdeskError
	with pytest.raises(FdeskError):
		lint.run_lint(app, ["L99"])


# ---- positive cases: (rule, severity, mutator)
def _c(rel, fn):
	return lambda app: edit(app, rel, fn)


def _item(d, label):
	return next(i for i in d["items"] if i.get("label") == label)


def m_l03(app):
	edit(app, SIDEBAR, lambda d: _item(d, "Work").update({"link_to": "Sample Ops", "link_type": "Workspace"}))


def m_l03_overview(app):
	edit(app, SIDEBAR, lambda d: d["items"].__delitem__(2))


def m_l04(app):
	edit(app, SIDEBAR, lambda d: d["items"].append({"child": 1, "indent": 0, "label": "Dup", "link_to": "Sample Doc", "link_type": "DocType", "type": "Link"}))


def m_l07(app):
	def f(d):
		d["shortcuts"] = []
	edit(app, HOME, f)


def m_l07_orphan(app):
	edit(app, HOME, lambda d: d["shortcuts"].append({"doc_view": "List", "label": "Extra", "link_to": "Sample Doc", "type": "DocType"}))


def m_l07_dup(app):
	def f(d):
		b = json.loads(d["content"])
		b[1]["id"] = b[0]["id"]
		d["content"] = json.dumps(b)
	edit(app, HOME, f)


def m_l08(app):
	def f(d):
		d["charts"] = [{"chart_name": "Ghost", "label": "Ghost"}]
	edit(app, OPS, f)


def m_l09(app):
	def f(d):
		del d["title"]
		d["standard"] = 0
	edit(app, OPS, f)


def m_l09_color(app):
	edit(app, OPS, lambda d: d.update({"indicator_color": "mauve"}))


def m_l10(app):
	edit(app, SIDEBAR, lambda d: d["items"].append({"child": 1, "indent": 0, "label": "Ext", "link_to": "https://x.io", "link_type": "URL", "type": "Link"}))


def m_l11(app):
	def f(d):
		d["items"][0]["is_default_module"] = 1
		d["items"][3]["is_default_module"] = 1
		d["items"][3]["link_to"] = "Sample Ops"
		d["items"][3]["link_type"] = "Workspace"
		d["items"][2]["is_default_module"] = 1
	edit(app, SIDEBAR, f)


def m_l13(app):
	import shutil
	shutil.rmtree(P(app, "dock"))


def m_l13_tile(app):
	edit(app, DOCK, lambda d: d["items"][0].update({"link_to": "Nope"}))


def m_l13_land(app):
	edit(app, SIDEBAR, lambda d: d["items"].__delitem__(slice(None, None)) or d["items"].append({"label": "x", "type": "Spacer"}))


def m_l14(app):
	def f(d):
		d["links"] = [{"type": "Card Break", "label": "C", "link_count": 3}, {"type": "Link", "label": "L", "link_type": "DocType", "link_to": "Sample Doc"}]
	edit(app, OPS, f)


def m_l14_req(app):
	edit(app, HOME, lambda d: d["shortcuts"].append({"type": "URL", "label": "U"}))


NC = {"name": "NC", "label": "NC", "module": "Sample Mod", "is_standard": 1, "type": "Document Type", "document_type": "Sample Doc", "function": "Count", "doctype": "Number Card"}
DC = {"name": "DC", "chart_name": "DC", "module": "Sample Mod", "is_standard": 1, "chart_type": "Count", "document_type": "Sample Doc", "based_on": "creation", "filters_json": "[]", "doctype": "Dashboard Chart"}


def put(app, kind, doc, mod="sample_mod"):
	p = P(app, f"{mod}/{kind}/{doc['name'].lower()}/{doc['name'].lower()}.json")
	p.parent.mkdir(parents=True)
	p.write_text(to_text(doc))
	return p


def m_l15(app):
	put(app, "number_card", {**NC, "function": "Sum"})
	put(app, "dashboard_chart", {k: v for k, v in DC.items() if k != "filters_json"})


def m_l15_report(app):
	put(app, "number_card", {**NC, "type": "Report"})


def m_l12(app):
	fx = P(app, "fixtures/custom_html_block.json")
	fx.parent.mkdir()
	fx.write_text(json.dumps([{"doctype": "Custom HTML Block", "name": "B", "html": "<script>x()</script><div></div>", "style": ".a{color:#fff}", "script": "document.querySelector('.a')", "private": 1, "roles": [{"role": "Sales User"}]}]))


def m_l01(app):
	(P(app, "sample_ops/workspace/sample_ops")).rename(P(app, "sample_ops/workspace/ops"))


def m_l01_title(app):
	edit(app, SIDEBAR, lambda d: d.update({"title": "Sample Ops", "name": "Sample Ops"}))


CASES = [
	("L01", "error", m_l01, {}),
	("L01", "error", m_l01_title, {}),
	("L03", "error", m_l03, {}),
	("L03", "warn", m_l03_overview, {}),
	("L04", "error", m_l04, {}),
	("L05", "error", lambda a: None, {"icon": lambda n, c: n != "home"}),
	("L06", "error", lambda a: None, {"cat": lambda k, n: n != "Sample Doc"}),
	("L07", "error", m_l07, {}),
	("L07", "warn", m_l07_orphan, {}),
	("L07", "error", m_l07_dup, {}),
	("L08", "error", m_l08, {"cat": lambda k, n: n != "Ghost"}),
	("L09", "error", m_l09, {}),
	("L09", "error", m_l09_color, {}),
	("L10", "error", m_l10, {}),
	("L11", "error", m_l11, {}),
	("L12", "error", m_l12, {}),
	("L12", "warn", m_l12, {}),
	("L12", "info", m_l12, {}),
	("L13", "error", m_l13, {}),
	("L13", "error", m_l13_tile, {"cat": lambda k, n: n != "Nope"}),
	("L13", "warn", m_l13_land, {}),
	("L14", "error", m_l14, {}),
	("L14", "error", m_l14_req, {}),
	("L15", "error", m_l15, {}),
	("L15", "error", m_l15_report, {}),
	("L16", "info", lambda a: None, {}),
]


@pytest.mark.parametrize("rule,sev,mut,kw", CASES, ids=[f"{c[0]}-{c[1]}-{c[2].__name__}" for c in CASES])
def test_positive(app, rule, sev, mut, kw):
	mut(app)
	assert rules(run(app, [rule], **kw), rule, sev), f"{rule}/{sev} not raised"


@pytest.mark.parametrize("rule", sorted(lint.RULES))
def test_negative_clean(app, rule):
	assert [f for f in run(app, [rule]) if f.severity != "info"] == []


def test_l05_duotone_only_on_dock(app):
	edit(app, SIDEBAR, lambda d: _item(d, "Home").update({"icon": "x-duotone"}))
	real = lambda n, c: not (n.endswith("-duotone") and c != "dock")
	assert rules(run(app, ["L05"], icon=real), "L05", "error")
	edit(app, SIDEBAR, lambda d: _item(d, "Home").update({"icon": "house"}))
	edit(app, DOCK, lambda d: d["items"][0].update({"icon": "x-duotone"}))
	assert not rules(run(app, ["L05"], icon=real), "L05", "error")


def test_l05_l06_none_is_info(app):
	fs = run(app, ["L05", "L06"], cat=lambda k, n: None, icon=lambda n, c: None)
	assert not [f for f in fs if f.severity != "info"]
	assert rules(fs, "L05", "info") and rules(fs, "L06", "info")


def test_l06_workspace_resolved_in_app(app):
	edit(app, SIDEBAR, lambda d: _item(d, "Overview").update({"link_to": "Sample Home"}))
	assert not rules(run(app, ["L06"], cat=lambda k, n: False if k == "workspace" else True), "L06", "error")
	edit(app, SIDEBAR, lambda d: _item(d, "Overview").update({"link_to": "Nowhere"}))
	assert rules(run(app, ["L06"], cat=lambda k, n: False if k == "workspace" else True), "L06", "error")


def test_l07_onboarding_and_all_types(app):
	def f(d):
		b = json.loads(d["content"])
		b.append({"id": "c1", "type": "onboarding", "data": {"onboarding_name": "Setup", "col": 12}})
		b.append({"id": "c2", "type": "quick_list", "data": {"quick_list_name": "QL", "col": 4}})
		d["content"] = json.dumps(b)
	edit(app, HOME, f)
	fs = rules(run(app, ["L07"]), "L07", "error")
	assert len(fs) == 2
	def g(d):
		d["module_onboarding"] = "Setup"
		d["quick_lists"] = [{"document_type": "Sample Doc", "label": "QL"}]
	edit(app, HOME, g)
	assert not rules(run(app, ["L07"]), "L07", "error")


# ---- git based rules
def test_l02_requires_bump(app):
	git(app, "init", "-q")
	git(app, "add", "-A")
	git(app, "commit", "-qm", "base")
	assert not rules(run(app, ["L02"]), "L02")
	edit(app, OPS, lambda d: d.update({"icon": "star"}))  # content change, same modified
	assert rules(run(app, ["L02"]), "L02", "error")
	edit(app, OPS, lambda d: d.update({"modified": "2026-06-01 10:00:00.000000"}))
	assert not rules(run(app, ["L02"]), "L02")


def test_l02_untracked_ok(app):
	git(app, "init", "-q")
	git(app, "add", "-A")
	git(app, "commit", "-qm", "base")
	put(app, "number_card", NC)
	assert not rules(run(app, ["L02"]), "L02")


def test_l08_deleted_file_needs_patch(app):
	put(app, "number_card", {**NC, "modified": "2026-01-01 10:00:00.000000"})
	git(app, "init", "-q")
	git(app, "add", "-A")
	git(app, "commit", "-qm", "base")
	(P(app, "sample_mod/number_card/nc/nc.json")).unlink()
	fs = rules(run(app, ["L08"]), "L08", "warn")
	assert fs and "add a patch" in fs[0].message


def test_l12_workspace_custom_block_missing(app):
	edit(app, HOME, lambda d: d.update({"custom_blocks": [{"custom_block_name": "Ghost", "label": "Ghost"}]}))
	assert rules(run(app, ["L12"], cat=lambda k, n: False), "L12", "error")


def test_perf_many_files(app):
	for i in range(50):
		put(app, "number_card", {**NC, "name": f"NC{i}", "label": f"NC{i}"})
	import time
	t = time.time()
	run(app)
	assert time.time() - t < 1.0


# ---- CLI
def cli(app, *a):
	return subprocess.run([sys.executable, str(FDESK), "--app", str(app.package), *a], capture_output=True, text=True)


def test_cli_exit_codes(app):
	r = cli(app, "lint")
	assert r.returncode == 0, r.stdout + r.stderr
	edit(app, OPS, lambda d: d.update({"standard": 0}))
	r = cli(app, "lint", "--only", "L09")
	assert r.returncode == 1 and "L09" in r.stdout
	assert cli(app, "lint", "--only", "L01").returncode == 0
	j = cli(app, "lint", "--format", "json", "--only", "L09")
	assert json.loads(j.stdout)["counts"]["error"] == 1 and j.returncode == 1
	assert cli(app, "lint", "--only", "L99").returncode == 2


def test_cli_strict_fails_on_warn(app):
	edit(app, SIDEBAR, lambda d: d["items"].__delitem__(2))  # L03 warn only
	assert cli(app, "lint", "--only", "L03").returncode == 0
	assert cli(app, "lint", "--only", "L03", "--strict").returncode == 1
	r = cli(app, "--json", "lint", "--only", "L03")
	assert json.loads(r.stdout)["ok"] is True


def test_l01_title_equal_to_own_module_allowed(app):
	edit(app, SIDEBAR, lambda d: d.update({"title": "Sample Mod", "name": "Sample Mod"}))
	assert not rules(run(app, ["L01"]), "L01", "error") or all("path must be" in f.message for f in rules(run(app, ["L01"]), "L01", "error"))


def test_l07_custom_block_row_label_must_equal_block_name(app):
	"""Frappe's custom block widget finds its child row by label; a differing label renders an empty block."""
	import json as _json

	from fdesk_lib import lint
	from fdesk_lib.common import dump, entity_path, load

	p = entity_path(app, "workspace", "Sample Home", "Sample Mod")
	doc = load(p)
	doc["content"] = _json.dumps([{"id": "cccccccccc", "type": "custom_block", "data": {"custom_block_name": "My Block", "col": 12}}])
	doc["custom_blocks"] = [{"custom_block_name": "My Block", "label": "Pretty Title"}]
	dump(p, doc)
	ctx = lint.Ctx(app, cat=lambda k, n: True, icon=lambda n, c: True)
	assert any(f.rule == "L07" for f in lint.RULES["L07"](app, ctx))
	doc["custom_blocks"] = [{"custom_block_name": "My Block", "label": "My Block"}]
	dump(p, doc)
	ctx = lint.Ctx(app, cat=lambda k, n: True, icon=lambda n, c: True)
	assert not [f for f in lint.RULES["L07"](app, ctx) if "My Block" in f.message]
