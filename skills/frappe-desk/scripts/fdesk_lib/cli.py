"""Argparse wiring. Each module exposes `register(subparsers)` and handlers `cmd_*(args) -> Result`; wired by the coordinator."""

from __future__ import annotations

import argparse
import importlib
import sys

from .common import FdeskError, apply

MODULES = ["sidebar", "dock", "workspace", "charts", "blocks", "icons", "catalog", "lint", "apply_bench"]


def build_parser() -> argparse.ArgumentParser:
	p = argparse.ArgumentParser(prog="fdesk", description="Deterministic edits for Frappe v16 desk navigation")
	p.add_argument("--app", default=None, help="path inside the app (default: cwd)")
	p.add_argument("--write", action="store_true", help="apply changes (default is dry-run with diff)")
	p.add_argument("--json", action="store_true", help="machine output")
	sub = p.add_subparsers(dest="group", required=True)
	for m in MODULES:
		try:
			mod = importlib.import_module(f"fdesk_lib.{m}")
		except ImportError:
			continue
		if hasattr(mod, "register"):
			mod.register(sub)
	return p


def main(argv) -> int:
	args = build_parser().parse_args(argv)
	try:
		res = args.func(args)
	except FdeskError as e:
		print(f"error: {e}", file=sys.stderr)
		return 2
	apply(res, args.write)
	if args.json:
		print(res.to_json())
	else:
		for c in res.changes:
			print(f"{c['action']}: {c['path']}")
			if c.get("diff") and not args.write:
				print(c["diff"])
		for w in res.warnings:
			print(f"warning: {w}")
		for e in res.errors:
			print(f"error: {e}")
		if not res.changes and not res.errors:
			print("no changes")
	return 0 if res.ok and not res.errors else 1
