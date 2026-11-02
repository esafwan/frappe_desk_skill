import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fdesk_lib import sidebar as sb
from fdesk_lib.common import FdeskError, apply, load

ROOT = Path(__file__).resolve().parent.parent
FDESK = ROOT / "skills" / "frappe-desk" / "scripts" / "fdesk"
NAME = "Sample App"


def ns(app, **kw):
	base = dict(app=str(app.root), write=True, json=True, sidebar=NAME, section=None, label=None, after=None, icon=None,
		to=None, url=None, link_type=None, default_module=False, route_options=None, filters=None, navigate_to_tab=None,
		open_in_new_tab=False, hidden=False, header=False, force=False, overview_workspace=None, new_label=None, order=None, app_rooted=False, module=None, title=None)
	base.update(kw)
	return argparse.Namespace(**base)


def sbpath(app):
	return app.package / "sample_mod" / "sidebar" / "sample_app" / "sample_app.json"


def run(fn, app, **kw):
	res = fn(ns(app, **kw))
	return apply(res, True)


def items(app):
	return load(sbpath(app))["items"]


def test_add_item_happy_idempotent_and_modified(app):
	mod0 = load(sbpath(app))["modified"]
	r = run(sb.cmd_add_item, app, section="Work", link_type="Report", to="Sample Report", label="Sample Rep", icon="file")
	assert len(r.changes) == 1
	row = [i for i in items(app) if i["label"] == "Sample Rep"][0]
	assert row["child"] == 1 and row["link_type"] == "Report" and row["link_to"] == "Sample Report"
	mod1 = load(sbpath(app))["modified"]
	assert mod1 > mod0
	text = sbpath(app).read_text()
	r2 = run(sb.cmd_add_item, app, section="Work", link_type="Report", to="Sample Report", label="Sample Rep", icon="file")
	assert r2.changes == [] and sbpath(app).read_text() == text


def test_add_item_url_default_module_after(app):
	run(sb.cmd_add_item, app, section="Work", link_type="URL", url="https://example.com", label="Docs", open_in_new_tab=True, after="Overview")
	labels = [i["label"] for i in items(app)]
	assert labels.index("Docs") == labels.index("Overview") + 1
	row = [i for i in items(app) if i["label"] == "Docs"][0]
	assert row["url"] == "https://example.com" and "link_to" not in row and row["open_in_new_tab"] == 1
	run(sb.cmd_add_item, app, section="Work", link_type="Page", to="p1", default_module=True)
	assert [i for i in items(app) if i["label"] == "p1"][0]["is_default_module"] == 1
	with pytest.raises(FdeskError):
		sb.cmd_add_item(ns(app, section="Work", link_type="URL", to="x"))


def test_add_item_duplicate_elsewhere_refused(app):
	run(sb.cmd_add_section, app, label="Other", icon="box")
	with pytest.raises(FdeskError):
		sb.cmd_add_item(ns(app, section="Other", link_type="DocType", to="Sample Doc"))
	with pytest.raises(FdeskError):  # same label, different target
		sb.cmd_add_item(ns(app, section="Work", link_type="DocType", to="Other Doc", label="Sample Doc"))
	# same target with distinct filters is a different identity
	run(sb.cmd_add_item, app, section="Other", link_type="DocType", to="Sample Doc", label="Filtered", filters='{"a": 1}')


def test_remove_move_rename_set_icon(app):
	run(sb.cmd_add_section, app, label="Other", icon="box", overview_workspace="Sample Home2")
	run(sb.cmd_move_item, app, label="Sample Doc", section="Other")
	labels = [i["label"] for i in items(app)]
	assert labels == ["Home", "Work", "Overview", "Other", "Overview", "Sample Doc"]
	t = sbpath(app).read_text()
	assert run(sb.cmd_move_item, app, label="Sample Doc", section="Other").changes == []
	assert sbpath(app).read_text() == t
	run(sb.cmd_rename_item, app, label="Sample Doc", new_label="Doc X")
	assert run(sb.cmd_rename_item, app, label="Sample Doc", new_label="Doc X").changes == []
	run(sb.cmd_set_icon, app, label="Doc X", section="Other", icon="file")
	run(sb.cmd_set_icon, app, header=True, icon="box")
	assert load(sbpath(app))["header_icon"] == "box"
	run(sb.cmd_remove_item, app, label="Doc X")
	assert run(sb.cmd_remove_item, app, label="Doc X").changes == []
	assert "Doc X" not in [i["label"] for i in items(app)]


def test_sections(app):
	run(sb.cmd_add_section, app, label="Two", icon="box", overview_workspace="Sample Home2")
	i = items(app)
	brk = [x for x in i if x["label"] == "Two"][0]
	assert brk["type"] == "Section Break" and brk["indent"] == 1 and "link_to" not in brk and "link_type" not in brk
	assert i[-1]["label"] == "Overview" and i[-1]["child"] == 1
	assert run(sb.cmd_add_section, app, label="Two", icon="box", overview_workspace="Sample Home2").changes == []
	with pytest.raises(FdeskError):  # Workspace already linked in this sidebar
		sb.cmd_add_section(ns(app, label="Three", icon="box", overview_workspace="Sample Home2"))
	run(sb.cmd_reorder_sections, app, order="Two,Work")
	assert [x["label"] for x in items(app)][:3] == ["Home", "Two", "Overview"]
	assert run(sb.cmd_reorder_sections, app, order="Two,Work").changes == []
	with pytest.raises(FdeskError):
		sb.cmd_reorder_sections(ns(app, order="Two"))
	with pytest.raises(FdeskError):
		sb.cmd_remove_section(ns(app, label="Work"))
	run(sb.cmd_remove_section, app, label="Work", force=True)
	assert "Work" not in [x["label"] for x in items(app)]
	assert run(sb.cmd_remove_section, app, label="Work", force=True).changes == []


def test_spacer_idempotent(app):
	run(sb.cmd_add_spacer, app, section="Work")
	assert [x for x in items(app) if x["type"] == "Spacer"][0]["child"] == 1
	assert run(sb.cmd_add_spacer, app, section="Work").changes == []


def test_list_and_new(app, capsys):
	r = sb.cmd_list(ns(app, json=False))
	assert r.info["sections"][0]["label"] == "Work" and len(r.info["sections"][0]["items"]) == 2
	assert "Work" in capsys.readouterr().out
	run(sb.cmd_new, app, title="Fresh", module="Sample Ops", icon="box")
	p = app.package / "sample_ops" / "sidebar" / "fresh" / "fresh.json"
	d = load(p)
	assert d["name"] == "Fresh" and d["module"] == "Sample Ops" and d["doctype"] == "Sidebar"
	assert run(sb.cmd_new, app, title="Fresh", module="Sample Ops", icon="box").changes == []
	with pytest.raises(FdeskError):
		sb.cmd_new(ns(app, title="Fresh", module="Sample Ops", icon="other"))
	run(sb.cmd_new, app, title="Rooted", module=None, icon="box", app_rooted=True)
	d = load(app.package / "sidebar" / "rooted" / "rooted.json")
	assert "module" not in d and d["app"] == "sample_app"
	with pytest.raises(FdeskError):
		sb.cmd_new(ns(app, title="Sample Mod", module="Sample Ops", icon="box"))


def test_hand_written_style_preserved(app):
	p = sbpath(app)
	d = load(p)
	p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")  # not Frappe canonical (unsorted, indent 2)
	before = p.read_text()
	run(sb.cmd_add_item, app, section="Work", link_type="Page", to="pg")
	after = p.read_text()
	assert after.startswith('{\n  "app"') or '\n  "' in after
	assert after.replace('"modified": "%s"' % load(p)["modified"], "") != before  # sanity
	assert '\n  "items": [' in after and '\n \"' not in after


def test_cli_subprocess(app):
	def cli(*a):
		out = subprocess.run([sys.executable, str(FDESK), "--app", str(app.root), *a], capture_output=True, text=True)
		return out
	args = ["sidebar", "add-item", NAME]
	# dry run leaves disk untouched
	o = cli("--json", "sidebar", "add-item", NAME, "--section", "Work", "--link-type", "Page", "--to", "pg")
	assert o.returncode == 0 and json.loads(o.stdout)["changes"] and "pg" not in sbpath(app).read_text()
	o = cli("--write", "--json", "sidebar", "add-item", NAME, "--section", "Work", "--link-type", "Page", "--to", "pg")
	assert o.returncode == 0 and "pg" in sbpath(app).read_text()
	o = cli("--write", "--json", "sidebar", "add-item", NAME, "--section", "Work", "--link-type", "Page", "--to", "pg")
	assert json.loads(o.stdout)["changes"] == []
	o = cli("--json", "sidebar", "add-item", NAME, "--section", "Work", "--link-type", "Page", "--to", "Sample Doc", "--label", "Sample Doc")
	assert o.returncode == 2
	o = cli("--json", "sidebar", "list", NAME)
	info = json.loads(o.stdout)["info"]
	assert info["sections"][0]["label"] == "Work"
