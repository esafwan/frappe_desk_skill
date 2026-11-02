import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fdesk_lib import apply_bench as ab
from fdesk_lib.common import FdeskError, apply as apply_res

FDESK = Path(__file__).resolve().parent.parent / "skills" / "frappe-desk" / "scripts" / "fdesk"


def make_bench(tmp_path, version="16.0.0"):
	b = tmp_path
	(b / "apps" / "frappe" / "frappe").mkdir(parents=True, exist_ok=True)
	(b / "apps" / "frappe" / "frappe" / "__init__.py").write_text(f'__version__ = "{version}"\n')
	return b


class Fake:
	def __init__(self, probe=None):
		self.cmds = []
		self.probe = probe or {"docs": {}, "overrides": [], "developer_mode": True}

	def __call__(self, argv):
		self.cmds.append(argv)
		if "FDESK_JSON:" in argv[-1] or "json.loads" in argv[-1]:
			return 0, "noise\n" + ab.MARK + json.dumps(self.probe) + "\n"
		return 0, "ok"


def ns(app, bench, **kw):
	d = dict(app=str(app.root), bench=str(bench), bench_cwd=None, site="s.local", runner=None,
		reset_nav=False, restart_cmd=None, write=True, json=True)
	d.update(kw)
	return argparse.Namespace(**d)


def test_v15_refused(app, tmp_path, monkeypatch):
	f = Fake()
	monkeypatch.setattr(ab, "run", f)
	with pytest.raises(FdeskError, match="v16"):
		ab.cmd_apply(ns(app, make_bench(tmp_path, "15.40.0")))
	assert f.cmds == []


def test_dry_run_runs_nothing_and_lists_commands(app, tmp_path, monkeypatch):
	f = Fake()
	monkeypatch.setattr(ab, "run", f)
	r = ab.cmd_apply(ns(app, make_bench(tmp_path), write=False, reset_nav=True))
	assert f.cmds == []
	joined = "\n".join(r.info["commands"])
	assert "migrate" in joined and "clear-cache" in joined and "reimport" in joined


def test_command_order_and_docker_runner(app, tmp_path, monkeypatch):
	f = Fake()
	monkeypatch.setattr(ab, "run", f)
	r = ab.cmd_apply(ns(app, make_bench(tmp_path), reset_nav=True, restart_cmd="supervisorctl restart all",
		runner="docker exec box bash -c", bench_cwd="/home/frappe/bench"))
	assert r.ok
	assert [c["step"] for c in r.info["ran"]] == ["probe", "migrate", "reimport", "clear-cache", "touch-hooks", "restart"]
	assert all(c[:4] == ["docker", "exec", "box", "bash"] for c in f.cmds)
	assert "cd /home/frappe/bench" in f.cmds[1][-1]
	assert "migrate" in f.cmds[1][-1] and "clear-cache" in f.cmds[3][-1] and "hooks.py" in f.cmds[4][-1]


def test_reset_nav_only_changed(app, tmp_path, monkeypatch):
	ents = ab.entities(app)
	docs = {f"{e['doctype']}|{e['name']}": {**e["doc"], "modified": "2099-01-01 00:00:00.000000"} for e in ents}
	f = Fake({"docs": docs, "overrides": [], "developer_mode": False})
	monkeypatch.setattr(ab, "run", f)
	r = ab.cmd_apply(ns(app, make_bench(tmp_path), reset_nav=True))
	assert r.info["changed"] == []
	assert "reset-nav" not in [c["step"] for c in r.info["ran"]]
	assert any("developer_mode" in w for w in r.warnings)


def test_verify_mismatch_and_ok(app, tmp_path, monkeypatch):
	ents = ab.entities(app)
	docs = {f"{e['doctype']}|{e['name']}": e["doc"] for e in ents}
	over = [{"name": "x", "module": "Sample Mod", "user": None, "label": "L"}]
	monkeypatch.setattr(ab, "run", Fake({"docs": docs, "overrides": over, "developer_mode": True}))
	r = ab.cmd_verify(ns(app, make_bench(tmp_path)))
	assert r.ok and r.info["mismatches"] == [] and r.info["overrides"] == over
	# break it: missing doc + modified diff
	keys = list(docs)
	bad = dict(docs)
	bad[keys[0]] = None
	k1 = keys[1]
	bad[k1] = {**docs[k1], "modified": "2000-01-01 00:00:00.000000"}
	monkeypatch.setattr(ab, "run", Fake({"docs": bad, "overrides": [], "developer_mode": True}))
	r = ab.cmd_verify(ns(app, make_bench(tmp_path)))
	probs = {m["problem"] for m in r.info["mismatches"]}
	assert not r.ok and {"missing in DB", "modified differs"} <= probs


def test_snapshot_restore_roundtrip_prune_idempotent(app):
	a = argparse.Namespace(app=str(app.root), name="s1", prune=False)
	r = ab.cmd_snapshot(a)
	apply_res(r, True)
	r2 = ab.cmd_snapshot(a)
	assert r2.changes == []  # identical content, no changes
	files = ab.nav_files(app)
	victim = files[0]
	orig = victim.read_text()
	victim.write_text(orig + " ")
	new = app.package / "sample_mod" / "workspace" / "extra" / "extra.json"
	new.parent.mkdir(parents=True)
	new.write_text("{}\n")
	r3 = ab.cmd_restore(a)
	assert any(str(victim) == c["path"] for c in r3.changes) and any("extra.json" in w for w in r3.warnings)
	apply_res(r3, True)
	import re as _re
	strip = lambda t: _re.sub(r'"modified": "[^"]*"', "", t)
	assert strip(victim.read_text()) == strip(orig) and new.exists()
	a.prune = True
	apply_res(ab.cmd_restore(a), True)
	assert not new.exists()
	assert ab.cmd_restore(a).changes == []


def test_cli_snapshot_subprocess(app):
	p = subprocess.run([sys.executable, str(FDESK), "--app", str(app.root), "--write", "--json", "snapshot", "--name", "c1"],
		capture_output=True, text=True)
	assert p.returncode == 0, p.stderr
	assert json.loads(p.stdout)["info"]["snapshot"].endswith("c1")
	assert (app.root / ".fdesk-snapshots" / "c1" / "manifest.json").exists()


def test_restore_rejects_bad_name_and_traversal(app):
	with pytest.raises(FdeskError, match="invalid snapshot name"):
		ab.cmd_restore(argparse.Namespace(app=str(app.root), name="../x", prune=False))
	a = argparse.Namespace(app=str(app.root), name="s2", prune=False)
	apply_res(ab.cmd_snapshot(a), True)
	mf = app.root / ".fdesk-snapshots" / "s2" / "manifest.json"
	m = json.loads(mf.read_text())
	m["files"]["../../evil/workspace/x.json"] = "0" * 64
	mf.write_text(json.dumps(m))
	with pytest.raises(FdeskError, match="outside"):
		ab.cmd_restore(a)


def test_restore_bumps_modified_so_migrate_will_import(app):
	a = argparse.Namespace(app=str(app.root), name="s3", prune=False)
	apply_res(ab.cmd_snapshot(a), True)
	victim = ab.nav_files(app)[0]
	doc = json.loads(victim.read_text())
	doc["icon"] = "changed-icon"
	doc["modified"] = "2026-06-01 00:00:00.000000"
	victim.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
	apply_res(ab.cmd_restore(a), True)
	assert json.loads(victim.read_text())["modified"] > "2026-06-01 00:00:00.000000"


def test_apply_force_imports_drift_without_deleting(app, tmp_path, monkeypatch):
	ents = ab.entities(app)
	e = next(x for x in ents if x["doctype"] == "Dock")
	db = {"doctype": "Dock", "name": e["name"], "modified": e["modified"], "items": []}
	f = Fake({"docs": {f"{x['doctype']}|{x['name']}": (db if x is e else x["doc"]) for x in ents}, "overrides": [], "developer_mode": True})
	monkeypatch.setattr(ab, "run", f)
	r = ab.cmd_apply(ns(app, make_bench(tmp_path)))
	joined = "\n".join(c[-1] for c in f.cmds)
	assert "import_file_by_path" in joined and "delete" not in joined.lower() and "force=True" in joined
	assert r.info["changed"] == [f"Dock|{e['name']}"]
