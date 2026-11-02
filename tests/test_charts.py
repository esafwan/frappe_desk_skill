import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fdesk_lib import charts as C
from fdesk_lib.common import FdeskError, apply, entity_path, load

FDESK = Path(__file__).resolve().parent.parent / "skills" / "frappe-desk" / "scripts" / "fdesk"

CH = dict(name="Orders", module="Sample Mod", chart_type="Count", document_type="Sample Doc", based_on="creation", group_by=None,
	group_by_type=None, value_based_on=None, number_of_groups=0, report=None, timeseries=True, time_interval="Monthly", timespan="Last Month",
	type=None, filters=None)
NC = dict(name="Open", module="Sample Mod", type="Document Type", document_type="Sample Doc", function=None, aggregate_field=None, filters=None,
	dynamic_filters=None, show_percentage_stats=False, report=None, report_field=None, report_function=None, method=None)


def ns(app, **kw):
	return argparse.Namespace(app=str(app.root), **kw)


def test_chart_new_idempotent_conflict(app):
	res = C.cmd_chart_new(ns(app, **CH))
	apply(res, True)
	p = entity_path(app, "dashboard_chart", "Orders", "Sample Mod")
	d = load(p)
	assert d["is_standard"] == 1 and d["module"] == "Sample Mod" and d["filters_json"] == "[]" and d["timeseries"] == 1
	assert d["time_interval"] == "Monthly" and d["type"] == "Line" and d["doctype"] == "Dashboard Chart"
	m = d["modified"]
	assert not C.cmd_chart_new(ns(app, **CH)).changes
	assert load(p)["modified"] == m
	with pytest.raises(FdeskError):
		C.cmd_chart_new(ns(app, **{**CH, "timespan": "Last Week"}))


def test_chart_validation(app):
	with pytest.raises(FdeskError):
		C.cmd_chart_new(ns(app, **{**CH, "chart_type": "Sum"}))  # needs value field
	with pytest.raises(FdeskError):
		C.cmd_chart_new(ns(app, **{**CH, "chart_type": "Group By", "group_by": None}))
	with pytest.raises(FdeskError):
		C.cmd_chart_new(ns(app, **{**CH, "chart_type": "Report"}))
	with pytest.raises(FdeskError):
		C.cmd_chart_new(ns(app, **{**CH, "filters": "{bad"}))
	r = C.cmd_chart_new(ns(app, **{**CH, "name": "G", "chart_type": "Group By", "group_by": "status", "timeseries": False, "time_interval": None, "timespan": None, "based_on": None}))
	assert "Bar" in r.changes[0]["_text"] and "group_by_based_on" in r.changes[0]["_text"]
	r = C.cmd_chart_new(ns(app, **{**CH, "name": "R", "chart_type": "Report", "report": "Rep", "document_type": None, "timeseries": False, "time_interval": None, "timespan": None, "based_on": None}))
	assert '"report_name": "Rep"' in r.changes[0]["_text"]


def test_number_card_types(app):
	apply(C.cmd_card_new(ns(app, **NC)), True)
	p = entity_path(app, "number_card", "Open", "Sample Mod")
	d = load(p)
	assert d["function"] == "Count" and d["is_standard"] == 1 and d["label"] == "Open" and d["filters_json"] == "[]"
	assert not C.cmd_card_new(ns(app, **NC)).changes
	with pytest.raises(FdeskError):
		C.cmd_card_new(ns(app, **{**NC, "function": "Sum"}))
	with pytest.raises(FdeskError):
		C.cmd_card_new(ns(app, **{**NC, "function": "Sum", "aggregate_field": "x"}))  # conflicts with existing Count
	r = C.cmd_card_new(ns(app, **{**NC, "name": "S", "function": "Sum", "aggregate_field": "total", "show_percentage_stats": True}))
	assert "aggregate_function_based_on" in r.changes[0]["_text"] and "stats_time_interval" in r.changes[0]["_text"]
	with pytest.raises(FdeskError):
		C.cmd_card_new(ns(app, **{**NC, "name": "Rp", "type": "Report", "report": "R"}))
	r = C.cmd_card_new(ns(app, **{**NC, "name": "Rp", "type": "Report", "report": "R", "report_field": "f", "report_function": "Sum"}))
	assert '"report_function": "Sum"' in r.changes[0]["_text"]
	with pytest.raises(FdeskError):
		C.cmd_card_new(ns(app, **{**NC, "name": "Cu", "type": "Custom"}))
	r = C.cmd_card_new(ns(app, **{**NC, "name": "Cu", "type": "Custom", "method": "a.b.c"}))
	assert '"method": "a.b.c"' in r.changes[0]["_text"]


def test_cli_subprocess(app):
	base = [sys.executable, str(FDESK), "--app", str(app.root), "--json"]
	args = ["number-card", "new", "Open", "--module", "Sample Mod", "--type", "Document Type", "--document-type", "Sample Doc"]
	r = subprocess.run([*base, "--write", *args], capture_output=True, text=True)
	assert r.returncode == 0 and json.loads(r.stdout)["changes"][0]["action"] == "create"
	r = subprocess.run([*base, *args], capture_output=True, text=True)
	assert json.loads(r.stdout)["changes"] == []
	r = subprocess.run([*base, "chart", "new", "C", "--module", "Sample Mod", "--chart-type", "Count", "--document-type", "Sample Doc", "--timeseries"], capture_output=True, text=True)
	assert r.returncode == 2
	r = subprocess.run([*base, "chart", "list"], capture_output=True, text=True)
	assert r.returncode == 0 and len(json.loads(r.stdout)["info"]["number_cards"]) == 1
