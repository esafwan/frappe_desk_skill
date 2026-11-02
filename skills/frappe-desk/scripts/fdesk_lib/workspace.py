"""Workspace edits. WP2 implements; signatures below are FROZEN (blocks.py and lint.py call them)."""

from __future__ import annotations

from .common import App, FdeskError, Result


def add_block(doc: dict, block_type: str, child_row: dict | None = None, data: dict | None = None, after_id: str | None = None) -> str:
	"""Append a content block of `block_type` (header|paragraph|spacer|shortcut|card|chart|number_card|quick_list|custom_block|onboarding)
	to doc['content'] (a JSON *string*), add `child_row` to the matching child table when given, return the new block id.
	Pure function on the dict: no I/O. Raises FdeskError on unknown type or duplicate label."""
	raise NotImplementedError


def content_blocks(doc: dict) -> list[dict]:
	"""Parsed doc['content'] (list of {id,type,data}); [] when empty."""
	raise NotImplementedError
