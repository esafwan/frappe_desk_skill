"""Self-healing catalog. WP3 implements; signatures FROZEN."""

from __future__ import annotations

from .common import App


def lookup(kind: str, name: str, app: App | None = None) -> bool | None:
	"""True if `name` exists for kind (doctype|report|page|workspace|dashboard|chart|number_card|custom_block|sidebar),
	False if the catalog is healthy and it does not, None if the catalog cannot answer (no frappe checkout): callers warn, never fail."""
	raise NotImplementedError


def refresh(app: App | None = None, force: bool = False) -> dict:
	"""Rebuild if fingerprint changed or cache unreadable; return {rebuilt: bool, fingerprint, counts}."""
	raise NotImplementedError
