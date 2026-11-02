import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

FDESK = ROOT / "skills" / "frappe-desk" / "scripts" / "fdesk"


def symbols(*ids):
	return '<svg id="x" style="display:none">' + "".join(f'<symbol viewBox="0 0 24 24" id="{i}"><path d="M0 0"/></symbol>' for i in ids) + "</svg>"


def make_bench(tmp_path, with_frappe=True):
	"""Fake bench: apps/frappe (sprites + aliases), apps/extra (own sprite via hooks), apps/sample_app, sites/."""
	bench = tmp_path / "bench"
	(bench / "sites").mkdir(parents=True)
	if with_frappe:
		pkg = bench / "apps" / "frappe" / "frappe"
		(pkg / "public" / "icons" / "lucide").mkdir(parents=True)
		(pkg / "public" / "icons" / "espresso").mkdir()
		(pkg / "public" / "icons" / "timeless").mkdir()
		(pkg / "hooks.py").write_text('app_include_icons = ["/assets/frappe/icons/lucide/icons.svg"]\n')
		(pkg / "modules.txt").write_text("Core\n")
		(pkg / "public" / "icons" / "lucide" / "icons.svg").write_text(symbols("icon-trash", "icon-users", "icon-sales-order"))
		(pkg / "public" / "icons" / "lucide" / "aliases.json").write_text(json.dumps({"trash-2": "trash"}))
		(pkg / "public" / "icons" / "espresso" / "icons.svg").write_text(symbols("es-line-filter"))
		(pkg / "public" / "icons" / "timeless" / "icons.svg").write_text(symbols("icon-up-line"))
		(pkg / "public" / "icons" / "module-icons.svg").write_text(symbols("icon-users-duotone"))
	extra = bench / "apps" / "extra" / "extra"
	(extra / "public" / "icons").mkdir(parents=True)
	(extra / "hooks.py").write_text('app_include_icons = ["/assets/extra/icons/mine.svg"]\n# app_include_icons = ["/assets/extra/nope.svg"]\n')
	(extra / "modules.txt").write_text("Extra\n")
	(extra / "public" / "icons" / "mine.svg").write_text(symbols("icon-factory-duotone", "icon-widget"))
	shutil.copytree(ROOT / "tests" / "fixtures" / "sample_app", bench / "apps" / "sample_app")
	return bench


@pytest.fixture
def bench(tmp_path, monkeypatch):
	monkeypatch.delenv("FDESK_BENCH", raising=False)
	return make_bench(tmp_path)


def bench_app(bench):
	from fdesk_lib.common import App
	return App.find(bench / "apps" / "sample_app")


def test_exists_union_alias_duotone(bench):
	from fdesk_lib import icons
	a = bench_app(bench)
	assert icons.exists("trash", app=a) is True
	assert icons.exists("icon-trash", app=a) is True
	assert icons.exists("trash-2", app=a) is True  # alias
	assert icons.exists("widget", app=a) is True  # other app via its hooks
	assert icons.exists("es-line-filter", app=a) is True
	assert icons.exists("up-line", app=a) is True
	assert icons.exists("nonsense", app=a) is False
	assert icons.exists("users-duotone", "sidebar", app=a) is False
	assert icons.exists("users-duotone", "workspace", app=a) is False
	assert icons.exists("users-duotone", "dock", app=a) is True
	assert icons.exists("factory-duotone", "dock", app=a) is True


def test_commented_hook_ignored(bench):
	from fdesk_lib import icons
	names, _ = icons.load_union(bench_app(bench))
	assert "nope" not in names


def test_env_bench_and_no_bench(app, tmp_path, monkeypatch):
	from fdesk_lib import icons
	monkeypatch.delenv("FDESK_BENCH", raising=False)
	assert icons.exists("trash", app=app) is None  # standalone app, no sprites
	b = make_bench(tmp_path / "other")
	monkeypatch.setenv("FDESK_BENCH", str(b))
	assert icons.exists("trash", app=app) is True


def test_search_ranking(bench):
	from fdesk_lib import icons
	a = bench_app(bench)
	r = icons.search("users", app=a)
	assert r[0] == "users" and "users-duotone" in r and r.index("users") < r.index("users-duotone")
	assert "trash-2" in icons.search("trash", app=a)
	assert icons.search("sales", limit=1, app=a) == ["sales-order"]
	assert icons.search("sale-ordr", app=a)[0] == "sales-order"  # fuzzy
	assert icons.search("", app=a) == []


def run(bench, *argv, env=None):
	import os
	e = {**os.environ, "FDESK_BENCH": str(bench)}
	return subprocess.run([sys.executable, str(FDESK), "--app", str(bench / "apps" / "sample_app"), *argv], capture_output=True, text=True, env=e)


def test_cli(bench):
	p = run(bench, "--json", "icons", "check", "trash")
	assert p.returncode == 0 and json.loads(p.stdout)["info"]["valid"] is True
	p = run(bench, "--json", "icons", "check", "users-duotone")
	assert p.returncode == 1 and "dock" in json.loads(p.stdout)["errors"][0]
	assert run(bench, "icons", "check", "users-duotone", "--context", "dock").returncode == 0
	p = run(bench, "--json", "icons", "search", "user", "--limit", "3")
	assert json.loads(p.stdout)["info"]["results"][0] == "users"
	p = run(bench, "--json", "icons", "list", "--prefix", "es-")
	assert json.loads(p.stdout)["info"]["icons"] == ["es-line-filter"]
	assert run(bench, "icons", "search", "widget").stdout.startswith("widget")
