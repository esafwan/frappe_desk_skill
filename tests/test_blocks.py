import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FDESK = ROOT / "skills" / "frappe-desk" / "scripts" / "fdesk"

from fdesk_lib import blocks, workspace  # noqa: E402
from fdesk_lib.common import App, FdeskError, apply, load  # noqa: E402


def ns(**kw):
	base = dict(app=None, kind="stat", name="open_todos", workspace="Sample Home", title=None, col=None, roles=None, param=None, json=False)
	base.update(kw)
	return argparse.Namespace(**base)


def run(handler, app, **kw):
	res = handler(ns(app=str(app.root), **kw))
	return apply(res, True)


def fixture(app):
	return json.loads((app.package / "fixtures" / "custom_html_block.json").read_text())


def ws_path(app):
	return app.package / "sample_mod" / "workspace" / "sample_home" / "sample_home.json"


@pytest.mark.parametrize("kind", blocks.KINDS)
def test_templates_pass_lint_and_have_no_placeholders(kind):
	rec = blocks.render_template(kind, 'My <b>"title"</b>')
	assert blocks.lint_block({"doctype": "Custom HTML Block", "name": "x", "private": 0, "roles": [], **rec}) == []
	assert "{{" not in rec["html"] + rec["style"] + rec["script"]
	assert "&lt;b&gt;" in rec["html"] or kind == "empty" and "&lt;b&gt;" in rec["html"]
	assert "root_element" in rec["script"] and "document." not in rec["script"].replace("block.ownerDocument.", "")
	assert "prefers-reduced-motion" in rec["style"] and "focus-visible" in rec["style"]


def test_new_happy_path_and_idempotent(app):
	hooks_before = (app.package / "hooks.py").read_text()
	run(blocks.cmd_new, app, roles="System Manager, Sales User", param=['doctype=Sales Invoice', 'filters={"docstatus": 1}'])
	recs = fixture(app)
	assert recs[0]["name"] == "open_todos" and recs[0]["private"] == 0
	assert recs[0]["roles"] == [{"role": "System Manager"}, {"role": "Sales User"}]
	assert '"doctype": "Sales Invoice"' in recs[0]["script"]
	hooks = (app.package / "hooks.py").read_text()
	assert hooks.startswith(hooks_before) and '"dt": "Custom HTML Block"' in hooks and '["name", "in", ["open_todos"]]' in hooks
	ws = load(ws_path(app))
	assert ws["custom_blocks"] == [{"custom_block_name": "open_todos", "label": "Total"}]
	assert any(b["type"] == "custom_block" and b["data"]["custom_block_name"] == "open_todos" for b in workspace.content_blocks(ws))
	snap = {p: p.read_bytes() for p in app.package.rglob("*") if p.is_file()}
	res = blocks.cmd_new(ns(app=str(app.root), roles="System Manager, Sales User", param=['doctype=Sales Invoice', 'filters={"docstatus": 1}']))
	assert res.changes == []
	apply(res, True)
	assert snap == {p: p.read_bytes() for p in app.package.rglob("*") if p.is_file()}


def test_conflict_and_bad_params(app):
	run(blocks.cmd_new, app)
	with pytest.raises(FdeskError, match="already exists"):
		blocks.cmd_new(ns(app=str(app.root), kind="list"))
	with pytest.raises(FdeskError, match="unknown --param"):
		blocks.cmd_new(ns(app=str(app.root), name="b2", param=["nope=1"]))
	with pytest.raises(FdeskError, match="invalid field"):
		blocks.cmd_new(ns(app=str(app.root), name="b2", param=["field=a;b"]))
	with pytest.raises(FdeskError, match="not found"):
		blocks.cmd_new(ns(app=str(app.root), name="b2", workspace="Nope"))


def test_two_blocks_sorted_and_hooks_extended(app):
	run(blocks.cmd_new, app, name="zeta", title="Z")
	run(blocks.cmd_new, app, name="alpha", kind="links", title="A")
	assert [r["name"] for r in fixture(app)] == ["alpha", "zeta"]
	assert '["name", "in", ["alpha", "zeta"]]' in (app.package / "hooks.py").read_text()


HOOK_CASES = [
	("app_name = 'x'\n", 'fixtures = [\n\t{"dt": "Custom HTML Block", "filters": [["name", "in", ["b"]]]},\n]\n'),
	("fixtures = []\n", '[\n\t{"dt": "Custom HTML Block", "filters": [["name", "in", ["b"]]]},\n]'),
	('fixtures = [\n\t"Role",\n]\n', '\t"Role",\n\t{"dt": "Custom HTML Block", "filters": [["name", "in", ["b"]]]},\n'),
	('fixtures = [\n\t"Role"\n]\n', '\t"Role",\n\t{"dt": "Custom HTML Block"'),
	('fixtures = [{"dt": "Custom HTML Block", "filters": [["name", "in", ["a"]]]}]\n', '["a", "b"]'),
	('fixtures = [{"dt": "Custom HTML Block", "filters": {"name": ["in", ["a"]]}}]\n', '{"name": ["in", ["a", "b"]]}'),
]


@pytest.mark.parametrize("src,expect", HOOK_CASES)
def test_edit_hooks(src, expect):
	new, _ = blocks.edit_hooks(src, lambda ex: sorted(set(ex) | {"b"}) if "a" in ex else ["b"])
	assert expect in new
	compile(new, "hooks.py", "exec")
	again, _ = blocks.edit_hooks(new, lambda ex: sorted(set(ex) | {"b"}))
	assert again == new


def test_edit_hooks_errors_and_warnings():
	with pytest.raises(FdeskError, match="not a plain list"):
		blocks.edit_hooks("fixtures = get()\n", lambda ex: ["b"])
	with pytest.raises(FdeskError, match="cannot parse the filters"):
		blocks.edit_hooks('fixtures = [{"dt": "Custom HTML Block", "filters": [["owner", "=", "x"]]}]\n', lambda ex: ["b"])
	new, warns = blocks.edit_hooks('fixtures = ["Custom HTML Block"]\n', lambda ex: ["b"])
	assert new == 'fixtures = ["Custom HTML Block"]\n' and warns


def test_remove_cleans_everything_and_is_idempotent(app):
	run(blocks.cmd_new, app, name="one")
	run(blocks.cmd_new, app, name="two", kind="empty")
	run(blocks.cmd_remove, app, name="one", workspace=None)
	assert [r["name"] for r in fixture(app)] == ["two"]
	ws = load(ws_path(app))
	assert [r["custom_block_name"] for r in ws["custom_blocks"]] == ["two"]
	assert '["two"]' in (app.package / "hooks.py").read_text()
	assert blocks.cmd_remove(ns(app=str(app.root), name="one", workspace=None)).changes == []
	run(blocks.cmd_remove, app, name="two", workspace=None)
	assert not (app.package / "fixtures" / "custom_html_block.json").exists()
	compile((app.package / "hooks.py").read_text(), "hooks.py", "exec")
	assert load(ws_path(app))["custom_blocks"] == []


def test_noop_leaves_modified_untouched(app):
	run(blocks.cmd_new, app)
	m = load(ws_path(app))["modified"]
	blocks.cmd_new(ns(app=str(app.root)))
	assert load(ws_path(app))["modified"] == m


def test_preserves_fixture_style(app):
	fx = app.package / "fixtures"
	fx.mkdir()
	other = {"doctype": "Custom HTML Block", "name": "zz_hand", "html": "<p>é</p>", "script": "", "style": "", "private": 0, "roles": []}
	(fx / "custom_html_block.json").write_text(json.dumps([other], indent=4, ensure_ascii=False) + "\n")
	run(blocks.cmd_new, app)
	text = (fx / "custom_html_block.json").read_text()
	assert text.startswith('[\n    {') and "é" in text
	assert [r["name"] for r in json.loads(text)] == ["open_todos", "zz_hand"]


def test_lint_block_rules():
	good = {"doctype": "Custom HTML Block", "name": "g", "html": "<p>x</p>", "style": "p{color:var(--text-color,#111)}", "script": "root_element.querySelector('p');", "private": 0, "roles": [{"role": "A"}]}
	assert blocks.lint_block(good) == []
	def bad(**kw):
		return blocks.lint_block({**good, **kw})
	assert any("external" in m for m in bad(script="fetch('https://x.io/a')"))
	assert any("external" in m for m in bad(style="@import url(x.css);p{color:var(--a)}"))
	assert any("hex" in m for m in bad(style="p{color:#fff}"))
	assert any("hex" in m for m in bad(html='<p style="color:#fff">x</p>'))
	assert any("rgb" in m for m in bad(style="p{color:rgb(1,2,3);margin:var(--x)}"))
	assert any("root_element" in m for m in bad(script="document.querySelector('p')"))
	assert any("root_element" in m for m in bad(script="el.addEventListener('x',f)"))
	assert any("private" in m for m in bad(private=1))
	assert any("duplicate" in m for m in bad(roles=[{"role": "A"}, {"role": "A"}]))
	assert any("invalid entry" in m for m in bad(roles=["A"]))
	assert any("<script>" in m for m in bad(html="<script>1</script>"))


def test_render_preview(app):
	run(blocks.cmd_new, app, kind="progress")
	res = blocks.cmd_preview(ns(app=str(app.root), json=True))
	page = res.info["html"]
	assert page.startswith("<!doctype html>") and 'data-theme="light"' in page and "attachShadow" in page and "frappeStub" in page
	with pytest.raises(FdeskError):
		blocks.cmd_preview(ns(app=str(app.root), name="missing", json=True))


def test_cli_subprocess(app):
	def cli(*a):
		return subprocess.run([sys.executable, str(FDESK), "--app", str(app.root), *a], capture_output=True, text=True)

	r = cli("--json", "block", "new", "--kind", "kpi-row", "--name", "kp", "--workspace", "Sample Home")
	assert r.returncode == 0 and json.loads(r.stdout)["changes"]  # dry run
	assert not (app.package / "fixtures").exists()
	assert cli("--write", "--json", "block", "new", "--kind", "kpi-row", "--name", "kp", "--workspace", "Sample Home").returncode == 0
	assert json.loads(cli("--json", "block", "new", "--kind", "kpi-row", "--name", "kp", "--workspace", "Sample Home").stdout)["changes"] == []
	lst = json.loads(cli("--json", "block", "list").stdout)
	assert lst["info"]["blocks"][0]["workspaces"] == ["Sample Home"] and lst["info"]["blocks"][0]["problems"] == []
	assert "block list" or "kp" in cli("block", "list").stdout
	p = cli("block", "render-preview", "--name", "kp")
	assert p.returncode == 0 and p.stdout.startswith("<!doctype html>")
	assert cli("block", "new", "--kind", "list", "--name", "kp", "--workspace", "Sample Home").returncode == 2
	assert cli("--write", "--json", "block", "remove", "--name", "kp").returncode == 0
	assert json.loads(cli("--json", "block", "list").stdout)["info"]["blocks"] == []
