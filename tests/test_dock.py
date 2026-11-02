import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fdesk_lib import dock
from fdesk_lib.common import FdeskError, apply, load

ROOT = Path(__file__).resolve().parent.parent
FDESK = ROOT / "skills" / "frappe-desk" / "scripts" / "fdesk"


def ns(app, **kw):
	base = dict(app=str(app.root), write=True, json=True, link_type=None, to=None, url=None, title=None, icon=None, after=None)
	base.update(kw)
	return argparse.Namespace(**base)


def dpath(app):
	return app.package / "dock" / "sample_app" / "sample_app.json"


def run(fn, app, **kw):
	return apply(fn(ns(app, **kw)), True)


def test_add_tile_idempotent_and_modified(app):
	m0 = load(dpath(app))["modified"]
	r = run(dock.cmd_add_tile, app, link_type="Workspace", to="Sample Ops", title="Ops", icon="settings")
	assert len(r.changes) == 1
	d = load(dpath(app))
	assert d["modified"] > m0 and d["items"][-1] == {"icon": "settings", "link_to": "Sample Ops", "link_type": "Workspace", "title": "Ops"}
	t = dpath(app).read_text()
	assert run(dock.cmd_add_tile, app, link_type="Workspace", to="Sample Ops", title="Ops", icon="settings").changes == []
	assert dpath(app).read_text() == t


def test_add_tile_url_after_and_conflicts(app):
	run(dock.cmd_add_tile, app, link_type="URL", url="https://x.io", title="Site", icon="globe", after="Sample")
	assert [i["title"] for i in load(dpath(app))["items"]] == ["Sample", "Site"]
	assert "link_to" not in load(dpath(app))["items"][1]
	with pytest.raises(FdeskError):
		dock.cmd_add_tile(ns(app, link_type="URL", url="https://y.io", title="Site", icon="globe"))
	with pytest.raises(FdeskError):
		dock.cmd_add_tile(ns(app, link_type="URL", url="https://x.io", title="Other", icon="globe"))
	with pytest.raises(FdeskError):
		dock.cmd_add_tile(ns(app, link_type="Sidebar", title="Z", icon="x"))


def test_set_icon_remove_list(app, capsys):
	run(dock.cmd_set_icon, app, title="Sample", icon="box")
	assert load(dpath(app))["items"][0]["icon"] == "box"
	assert run(dock.cmd_set_icon, app, title="Sample", icon="box").changes == []
	with pytest.raises(FdeskError):
		dock.cmd_set_icon(ns(app, title="Nope", icon="box"))
	r = dock.cmd_list(ns(app, json=False))
	assert r.info["tiles"][0]["title"] == "Sample" and "Sample" in capsys.readouterr().out
	run(dock.cmd_remove_tile, app, title="Sample")
	assert run(dock.cmd_remove_tile, app, title="Sample").changes == []


def test_creates_missing_dock_canonical(app):
	dpath(app).unlink()
	run(dock.cmd_add_tile, app, link_type="Sidebar", to="Sample App", title="S", icon="home")
	d = load(dpath(app))
	assert d["doctype"] == "Dock" and d["name"] == "sample_app" and d["standard"] == 1
	assert dpath(app).read_text().startswith('{\n "app"')


def test_hand_written_style_preserved(app):
	d = load(dpath(app))
	dpath(app).write_text(json.dumps(d, indent=4) + "\n")
	run(dock.cmd_add_tile, app, link_type="Workspace", to="Sample Ops", title="Ops", icon="settings")
	assert '\n    "items": [' in dpath(app).read_text()


def test_cli_subprocess(app):
	def cli(*a):
		return subprocess.run([sys.executable, str(FDESK), "--app", str(app.root), *a], capture_output=True, text=True)
	o = cli("--write", "--json", "dock", "add-tile", "--link-type", "Workspace", "--to", "Sample Ops", "--title", "Ops", "--icon", "settings")
	assert o.returncode == 0 and len(json.loads(o.stdout)["changes"]) == 1
	o = cli("--write", "--json", "dock", "add-tile", "--link-type", "Workspace", "--to", "Sample Ops", "--title", "Ops", "--icon", "settings")
	assert json.loads(o.stdout)["changes"] == []
	o = cli("--json", "dock", "list")
	assert [t["title"] for t in json.loads(o.stdout)["info"]["tiles"]] == ["Sample", "Ops"]
	o = cli("--json", "dock", "add-tile", "--link-type", "URL", "--to", "x", "--title", "Q", "--icon", "a")
	assert o.returncode == 2
