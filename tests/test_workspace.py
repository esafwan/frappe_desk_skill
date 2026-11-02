import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fdesk_lib import workspace as W
from fdesk_lib.common import FdeskError, apply, entity_path, load

FDESK = Path(__file__).resolve().parent.parent / "skills" / "frappe-desk" / "scripts" / "fdesk"


def ns(app, **kw):
	return argparse.Namespace(app=str(app.root), **kw)


def run(fn, app, **kw):
	res = fn(ns(app, **kw))
	apply(res, True)
	return res


def ws_path(app, name="Sample Home", mod="Sample Mod"):
	return entity_path(app, "workspace", name, mod)


def test_add_block_pure_and_deterministic():
	doc = {"content": "[]", "shortcuts": []}
	a = W.add_block(doc, "shortcut", {"type": "DocType", "label": "X", "link_to": "X"}, {"col": 3})
	doc2 = {"content": "[]", "shortcuts": []}
	assert W.add_block(doc2, "shortcut", {"type": "DocType", "label": "X", "link_to": "X"}, {"col": 3}) == a
	assert len(a) == 10 and W.content_blocks(doc)[0]["data"]["shortcut_name"] == "X"
	with pytest.raises(FdeskError):
		W.add_block(doc, "shortcut", {"type": "DocType", "label": "X", "link_to": "X"})
	with pytest.raises(FdeskError):
		W.add_block(doc, "bogus")
	h1 = W.add_block(doc, "spacer")
	h2 = W.add_block(doc, "spacer")
	assert h1 != h2
	assert W.content_blocks({}) == []


def test_new_idempotent_and_conflict(app):
	kw = dict(name="Fresh", module="Sample Ops", icon="home", label=None)
	run(W.cmd_new, app, **kw)
	d = load(ws_path(app, "Fresh", "Sample Ops"))
	assert d["standard"] == 1 and d["public"] == 1 and d["type"] == "Workspace" and d["title"] == "Fresh"
	assert not W.cmd_new(ns(app, **kw)).changes
	with pytest.raises(FdeskError):
		W.cmd_new(ns(app, **{**kw, "icon": "other"}))


SC = dict(workspace="Sample Home", type="DocType", to="Other Doc", url=None, label="Other", doc_view=None, color=None, stats_filter=None, col=3, after=None)


def test_shortcut_add_idempotent_conflict_remove(app):
	p = ws_path(app)
	run(W.cmd_add_shortcut, app, **SC)
	d = load(p)
	assert [r["label"] for r in d["shortcuts"]] == ["Sample Doc", "Other"]
	assert d["shortcuts"][1]["doc_view"] == "List"
	assert W.content_blocks(d)[-1]["data"]["shortcut_name"] == "Other"
	m = d["modified"]
	assert not W.cmd_add_shortcut(ns(app, **SC)).changes
	assert load(p)["modified"] == m
	with pytest.raises(FdeskError):
		W.cmd_add_shortcut(ns(app, **{**SC, "to": "Different"}))
	run(W.cmd_remove_shortcut, app, workspace="Sample Home", label="Other")
	d = load(p)
	assert len(d["shortcuts"]) == 1 and len(W.content_blocks(d)) == 2
	assert not W.cmd_remove_shortcut(ns(app, workspace="Sample Home", label="Other")).changes


def test_style_preserved_on_hand_written_file(app):
	p = ws_path(app)
	run(W.cmd_add_shortcut, app, **SC)
	content = load(p)["content"]
	assert content.startswith('[{"id": "aaaaaaaaaa", "type": "header"')  # spaced separators kept
	assert p.read_text().endswith("\n")


def test_cards_and_links_count(app):
	p = ws_path(app)
	run(W.cmd_add_link_card, app, workspace="Sample Home", label="Docs", icon=None, col=4)
	run(W.cmd_add_link, app, workspace="Sample Home", card="Docs", link_type="DocType", to="Sample Doc", label="A", onboard=False, dependencies=None, is_query_report=False)
	run(W.cmd_add_link, app, workspace="Sample Home", card="Docs", link_type="Report", to="R", label="B", onboard=True, dependencies=None, is_query_report=True)
	run(W.cmd_add_link_card, app, workspace="Sample Home", label="Other", icon="x", col=4)
	run(W.cmd_add_link, app, workspace="Sample Home", card="Docs", link_type="DocType", to="Z", label="C", onboard=False, dependencies=None, is_query_report=False)
	d = load(p)
	labels = [(r["type"], r["label"]) for r in d["links"]]
	assert labels == [("Card Break", "Docs"), ("Link", "A"), ("Link", "B"), ("Link", "C"), ("Card Break", "Other")]
	assert d["links"][0]["link_count"] == 3 and d["links"][4]["link_count"] == 0
	assert [b["data"]["card_name"] for b in W.content_blocks(d) if b["type"] == "card"] == ["Docs", "Other"]
	m = d["modified"]
	assert not W.cmd_add_link(ns(app, workspace="Sample Home", card="Docs", link_type="DocType", to="Sample Doc", label="A", onboard=False, dependencies=None, is_query_report=False)).changes
	with pytest.raises(FdeskError):
		W.cmd_add_link(ns(app, workspace="Sample Home", card="Docs", link_type="DocType", to="Other", label="A", onboard=False, dependencies=None, is_query_report=False))
	with pytest.raises(FdeskError):
		W.cmd_add_link(ns(app, workspace="Sample Home", card="Nope", link_type="DocType", to="Q", label="Q", onboard=False, dependencies=None, is_query_report=False))
	run(W.cmd_remove_link, app, workspace="Sample Home", card="Docs", label="B")
	d = load(p)
	assert d["links"][0]["link_count"] == 2 and load(p)["modified"] != m
	assert not W.cmd_remove_link(ns(app, workspace="Sample Home", card="Docs", label="B")).changes


def test_widgets_and_text_blocks(app):
	p = ws_path(app)
	run(W.cmd_add_chart, app, workspace="Sample Home", chart="Sales", label=None, col=12)
	run(W.cmd_add_number_card, app, workspace="Sample Home", card="Count X", label="X count", col=4)
	run(W.cmd_add_quick_list, app, workspace="Sample Home", doctype="Sample Doc", label="Recent", col=6)
	run(W.cmd_add_header, app, workspace="Sample Home", text="Hello", after=None)
	run(W.cmd_add_paragraph, app, workspace="Sample Home", text="Para", after=None)
	run(W.cmd_add_spacer, app, workspace="Sample Home", after=None)
	d = load(p)
	assert d["charts"] == [{"chart_name": "Sales", "label": "Sales"}]
	assert d["number_cards"][0]["label"] == "X count"
	types = [b["type"] for b in W.content_blocks(d)]
	assert types == ["header", "shortcut", "chart", "number_card", "quick_list", "header", "paragraph", "spacer"]
	nc = [b for b in W.content_blocks(d) if b["type"] == "number_card"][0]
	assert nc["data"]["number_card_name"] == "X count"  # block references the row label
	for fn, kw in [(W.cmd_add_chart, dict(chart="Sales", label=None, col=12)), (W.cmd_add_header, dict(text="Hello", after=None)),
			(W.cmd_add_spacer, dict(after=None)), (W.cmd_add_quick_list, dict(doctype="Sample Doc", label="Recent", col=6))]:
		assert not fn(ns(app, workspace="Sample Home", **kw)).changes
	with pytest.raises(FdeskError):
		W.cmd_add_chart(ns(app, workspace="Sample Home", chart="Other", label="Sales", col=12))


def test_misc_commands_and_move(app):
	p = ws_path(app)
	run(W.cmd_set_icon, app, workspace="Sample Home", icon="star")
	run(W.cmd_set_roles, app, workspace="Sample Home", roles="A,B")
	run(W.cmd_hide, app, workspace="Sample Home", unhide=False)
	d = load(p)
	assert d["icon"] == "star" and d["roles"] == [{"role": "A"}, {"role": "B"}] and d["is_hidden"] == 1
	assert not W.cmd_set_roles(ns(app, workspace="Sample Home", roles="A,B")).changes
	run(W.cmd_hide, app, workspace="Sample Home", unhide=True)
	assert load(p)["is_hidden"] == 0
	run(W.cmd_move_block, app, workspace="Sample Home", id="aaaaaaaaaa", after="bbbbbbbbbb")
	assert [b["id"] for b in W.content_blocks(load(p))] == ["bbbbbbbbbb", "aaaaaaaaaa"]
	assert not W.cmd_move_block(ns(app, workspace="Sample Home", id="aaaaaaaaaa", after="bbbbbbbbbb")).changes
	with pytest.raises(FdeskError):
		W.cmd_move_block(ns(app, workspace="Sample Home", id="zz", after="aaaaaaaaaa"))
	assert W.cmd_list(ns(app, workspace="Sample Home")).info["shortcuts"] == ["Sample Doc"]
	with pytest.raises(FdeskError):
		W.cmd_hide(ns(app, workspace="Missing", unhide=False))


def test_cli_subprocess(app):
	base = [sys.executable, str(FDESK), "--app", str(app.root), "--json"]
	r = subprocess.run([*base, "workspace", "add-chart", "Sample Home", "--chart", "Sales"], capture_output=True, text=True)
	assert r.returncode == 0 and len(json.loads(r.stdout)["changes"]) == 1
	r = subprocess.run([*base, "--write", "workspace", "add-chart", "Sample Home", "--chart", "Sales"], capture_output=True, text=True)
	assert r.returncode == 0
	r = subprocess.run([*base, "workspace", "add-chart", "Sample Home", "--chart", "Sales"], capture_output=True, text=True)
	assert json.loads(r.stdout)["changes"] == []
	r = subprocess.run([*base, "workspace", "add-link", "Sample Home", "--card", "none", "--link-type", "DocType", "--to", "A", "--label", "A"], capture_output=True, text=True)
	assert r.returncode == 2
