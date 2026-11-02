"""Icon lookup. WP3 implements; signatures FROZEN."""

from __future__ import annotations


def exists(name: str, context: str = "sidebar", app=None) -> bool | None:
	"""context: sidebar|workspace|dock. Union of all installed apps' app_include_icons sprites + lucide aliases.
	'-duotone' names are valid only for context == 'dock'. None when no sprite could be found (warn, not fail)."""
	raise NotImplementedError


def search(query: str, limit: int = 20, app=None) -> list[str]:
	raise NotImplementedError
