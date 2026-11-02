import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import ROOT
from test_icons import FDESK, make_bench

DT = {"name": "Foo Thing", "module": "Core", "issingle": 1, "istable": 0}


def write_entity(pkg, module, kind, name, body):
	from fdesk_lib.common import scrub
	d = pkg / scrub(module) / kind / scrub(name)
	d.mkdir(parents=True, exist_ok=True)
	(d / f"{scrub(name)}.json").write_text(json.dumps(body, indent=1, sort_keys=True) + "\n")


@pytest.fixture
def bench(tmp_path, monkeypatch):
	monkeypatch.delenv("FDESK_BENCH", raising=False)
	b = make_bench(tmp_path)
	fpkg = b / "apps" / "frappe" / "frappe"
	write_entity(fpkg, "Core", "doctype", "Foo Thing", DT)
	write_entity(fpkg, "Core", "doctype", "Row Thing", {"name": "Row Thing", "module": "Core", "istable": 1})
	write_entity(fpkg, "Core", "report", "Foo Report", {"report_name": "Foo Report", "ref_doctype": "Foo Thing", "name": "Foo Report"})
	write_entity(fpkg, "Core", "page", "foo-page", {"name": "foo-page", "page_name": "foo-page"})
	write_entity(fpkg, "Core", "number_card", "Foo Card", {"name": "Foo Card"})
	write_entity(fpkg, "Core", "dashboard_chart", "Foo Chart", {"name": "Foo Chart"})
	return b


@pytest.fixture
def napp(bench):
	from fdesk_lib.common import App
	return App.find(bench / "apps" / "sample_app")


def cache(app):
	return app.root / ".fdesk-cache" / "catalog.json"


def test_lookup_kinds(napp):
	from fdesk_lib import catalog
	assert catalog.lookup("doctype", "Foo Thing", napp) is True
	assert catalog.lookup("doctype", "Nope", napp) is False
	assert catalog.lookup("report", "Foo Report", napp) is True
	assert catalog.lookup("page", "foo-page", napp) is True
	assert catalog.lookup("number_card", "Foo Card", napp) is True
	assert catalog.lookup("chart", "Foo Chart", napp) is True
	assert catalog.lookup("dashboard_chart", "Foo Chart", napp) is True
	# the app's own sidebar/workspace are cataloged too
	assert catalog.lookup("workspace", "Sample Home", napp) is True or catalog.lookup("workspace", "sample_home", napp) is not None
	cat = json.loads(cache(napp).read_text())
	assert cat["items"]["doctype"]["Foo Thing"]["issingle"] == 1
	assert cat["items"]["doctype"]["Row Thing"]["istable"] == 1
	assert cat["items"]["report"]["Foo Report"]["ref_doctype"] == "Foo Thing"


def test_no_bench_returns_none(app, monkeypatch):
	from fdesk_lib import catalog
	monkeypatch.delenv("FDESK_BENCH", raising=False)
	assert catalog.lookup("doctype", "Anything", app) is None


def test_refresh_cached_then_stale(bench, napp):
	from fdesk_lib import catalog
	r1 = catalog.refresh(napp)
	assert r1["rebuilt"] and cache(napp).exists()
	r2 = catalog.refresh(napp)
	assert not r2["rebuilt"] and r2["fingerprint"] == r1["fingerprint"]
	assert catalog.refresh(napp, force=True)["rebuilt"]
	write_entity(bench / "apps" / "frappe" / "frappe", "Core", "doctype", "Late One", {"name": "Late One", "module": "Core"})
	assert catalog.lookup("doctype", "Late One", napp) is True  # stale fingerprint -> rebuilt
	# edit of an existing file (same name, different size)
	write_entity(bench / "apps" / "frappe" / "frappe", "Core", "doctype", "Late One", {"name": "Late One", "module": "Core", "issingle": 1})
	assert catalog.refresh(napp)["rebuilt"]


@pytest.mark.parametrize("corruption", [
	lambda p: p.unlink(),
	lambda p: p.write_text(p.read_text()[:40]),
	lambda p: p.write_text(""),
	lambda p: p.write_text("[1, 2]"),
	lambda p: p.write_text("null"),
	lambda p: p.write_bytes(b"\xff\xfe\x00garbage"),
	lambda p: p.write_text(json.dumps({**json.loads(p.read_text()), "schema": 999})),
	lambda p: p.write_text(json.dumps({**json.loads(p.read_text()), "items": "bad"})),
	lambda p: p.write_text(json.dumps({**json.loads(p.read_text()), "fingerprint": "stale"})),
])
def test_self_heals(napp, corruption):
	from fdesk_lib import catalog
	catalog.refresh(napp)
	p = cache(napp)
	corruption(p)
	assert catalog.lookup("doctype", "Foo Thing", napp) is True
	cat = json.loads(p.read_text())  # repaired, valid JSON again
	assert cat["schema"] == catalog.SCHEMA and "Foo Thing" in cat["items"]["doctype"]
	assert not [f for f in p.parent.iterdir() if f.suffix == ".tmp"]


def test_cache_dir_is_a_file_does_not_crash(napp):
	from fdesk_lib import catalog
	(napp.root / ".fdesk-cache").write_text("i am a file")
	assert catalog.lookup("doctype", "Foo Thing", napp) is True
	assert catalog.refresh(napp)["persisted"] is False


def test_deleted_app_dir(bench, napp):
	from fdesk_lib import catalog
	extra = bench / "apps" / "extra"
	write_entity(extra / "extra", "Extra", "doctype", "Extra Doc", {"name": "Extra Doc", "module": "Extra"})
	assert catalog.lookup("doctype", "Extra Doc", napp) is True
	shutil.rmtree(extra)
	assert catalog.lookup("doctype", "Extra Doc", napp) is False
	assert catalog.lookup("doctype", "Foo Thing", napp) is True


def test_bench_without_frappe_is_unknown(tmp_path, monkeypatch):
	from fdesk_lib import catalog
	from fdesk_lib.common import App
	monkeypatch.delenv("FDESK_BENCH", raising=False)
	b = make_bench(tmp_path, with_frappe=False)
	assert catalog.lookup("doctype", "X", App.find(b / "apps" / "sample_app")) is None


def test_git_head_in_fingerprint(bench, napp):
	from fdesk_lib import catalog
	f1 = catalog.refresh(napp)["fingerprint"]
	repo = bench / "apps" / "extra"
	(repo / ".git").mkdir()
	(repo / ".git" / "HEAD").write_text("abc123\n")
	assert catalog.refresh(napp)["fingerprint"] != f1


def test_unknown_kind(napp):
	from fdesk_lib import catalog
	from fdesk_lib.common import FdeskError
	with pytest.raises(FdeskError):
		catalog.lookup("bogus", "x", napp)


def run(bench, *argv):
	e = {**os.environ, "FDESK_BENCH": str(bench)}
	return subprocess.run([sys.executable, str(FDESK), "--app", str(bench / "apps" / "sample_app"), *argv], capture_output=True, text=True, env=e)


def test_cli(bench):
	p = run(bench, "--json", "catalog", "status")
	assert json.loads(p.stdout)["info"]["state"] == "missing"
	p = run(bench, "--json", "catalog", "refresh")
	i = json.loads(p.stdout)["info"]
	assert p.returncode == 0 and i["rebuilt"] and i["counts"]["doctype"] >= 2
	assert json.loads(run(bench, "--json", "catalog", "refresh").stdout)["info"]["rebuilt"] is False
	assert json.loads(run(bench, "--json", "catalog", "status").stdout)["info"]["state"] == "fresh"
	p = run(bench, "--json", "catalog", "search", "foo", "--kind", "doctype")
	assert [r["name"] for r in json.loads(p.stdout)["info"]["results"]] == ["Foo Thing"]
	assert "write" in run(bench, "catalog", "--help").stdout.lower()
	cache_file = bench / "apps" / "sample_app" / ".fdesk-cache" / "catalog.json"
	cache_file.write_text("{")
	assert json.loads(run(bench, "--json", "catalog", "status").stdout)["info"]["state"] == "corrupt"
	assert json.loads(run(bench, "--json", "catalog", "refresh").stdout)["info"]["rebuilt"] is True
