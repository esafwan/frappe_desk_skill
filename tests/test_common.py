import json
import os
from pathlib import Path

import pytest

from fdesk_lib.common import App, detect_style, render, FdeskError, bump_modified, dump, entity_path, load, plan_write, Result, scrub, to_text


def test_scrub():
	assert scrub("Staff & Shifts") == "staff_&_shifts"
	assert scrub("URY Plan") == "ury_plan"
	assert scrub("pos-profile") == "pos_profile"


def test_format_is_frappe_export_format():
	text = to_text({"b": 1, "a": None, "z": "é", "items": [{"idx": 1, "name": "x", "label": "L", "none": None}]})
	assert text == '{\n "b": 1,\n "items": [\n  {\n   "label": "L"\n  }\n ],\n "z": "\\u00e9"\n}\n'


def test_roundtrip_fixture_is_byte_equal(app):
	for p in app.package.rglob("*.json"):
		assert to_text(load(p)) == p.read_text(), p


def test_find_app_from_nested_path(app):
	assert App.find(app.package / "sample_mod" / "workspace").name == "sample_app"
	with pytest.raises(FdeskError):
		App.find("/")


def test_entity_paths(app):
	assert entity_path(app, "workspace", "Sample Home", "Sample Mod").name == "sample_home.json"
	assert "sample_mod/sidebar/sample_app" in str(entity_path(app, "sidebar", "Sample App", "Sample Mod"))
	assert str(entity_path(app, "sidebar", "Sample App", app_rooted=True)).endswith("sample_app/sidebar/sample_app/sample_app.json")
	assert str(entity_path(app, "dock", "sample_app")).endswith("sample_app/dock/sample_app/sample_app.json")


def test_bump_is_strictly_greater():
	d = {"modified": "2999-01-01 00:00:00.000000"}
	assert bump_modified(d, "2999-01-01 00:00:00.000000") > "2999-01-01 00:00:00.000000"


def test_noop_does_not_bump(app):
	p = entity_path(app, "workspace", "Sample Home", "Sample Mod")
	res = Result()
	plan_write(res, p, load(p))
	assert res.changes == []


def test_change_bumps_and_diffs(app):
	p = entity_path(app, "workspace", "Sample Home", "Sample Mod")
	doc = load(p)
	doc["icon"] = "chart-line"
	res = Result()
	plan_write(res, p, doc)
	assert len(res.changes) == 1 and "chart-line" in res.changes[0]["diff"]
	assert json.loads(res.changes[0]["_text"])["modified"] > "2026-01-01 10:00:00.000000"


REAL = os.environ.get("FDESK_REAL_APPS")


@pytest.mark.skipif(not REAL, reason="set FDESK_REAL_APPS to a bench apps dir for the byte round-trip over real files")
def test_roundtrip_real_apps():
	bad = []
	n = 0
	for kind in ("sidebar", "dock", "workspace", "number_card", "dashboard_chart"):
		for p in Path(REAL).glob(f"*/*/*/{kind}/*/*.json"):
			n += 1
			text = p.read_text()
			if render(load(p), detect_style(text)) != text:
				bad.append(str(p))
	assert n > 20 and not bad, bad[:5]


def test_edit_preserves_hand_written_style(tmp_path):
	p = tmp_path / "w.json"
	p.write_text('{\n\t"name": "X",\n\t"modified": "2026-01-01 00:00:00.000000",\n\t"b": "é",\n\t"a": 1\n}\n', encoding="utf-8")
	doc = load(p)
	doc["a"] = 2
	res = Result()
	plan_write(res, p, doc)
	text = res.changes[0]["_text"]
	assert text.startswith('{\n\t"name"') and '"é"' in text and text.index('"b"') < text.index('"a"')
