# Architecture

## Layout
```
skills/frappe-desk/
  SKILL.md            router for agents
  references/         mechanics, recipes, gotchas, icons, custom-blocks
  scripts/fdesk       entry point (calls fdesk_lib.cli)
  scripts/fdesk_lib/  implementation (stdlib only)
  templates/blocks/   Custom HTML Block templates (html + json)
tests/                pytest; fixtures/sample_app is a copy-on-use fixture app
```

## Module map
| Module | Responsibility |
|---|---|
| `common.py` | `App.find`, `load`/`dump`, `scrub`, `bump_modified`, `entity_path`, `plan_write`, `apply`, `unified_diff`, `Result`, `FdeskError`, `git_head_text`. Frozen API |
| `cli.py` | argparse root: global `--app --write --json`; registers each module's `register(sub)`; runs handlers, prints diff/JSON, applies `Result` when `--write` |
| `sidebar.py`, `dock.py` | Sidebar and Dock mutations |
| `workspace.py` | Workspace child rows and `content` blocks; exposes `add_block` and `content_blocks` |
| `charts.py` | Number Card and Dashboard Chart creation |
| `blocks.py` | Custom HTML Block files: fixture merge, hooks entry, workspace wiring (calls `workspace.add_block`) |
| `icons.py` | icon index (sprites + aliases), `exists`, `search` |
| `catalog.py` | cache of linkable entities, `lookup`, `refresh` |
| `lint.py` | rules L01-L16 |
| `apply.py` | `apply`, `verify`, `snapshot`, `restore` against a bench |

## Data flow
```
argv -> cli -> handler(args) -> App.find -> load(json) -> mutate dict
      -> plan_write(res, path, new_doc)   # style-preserving text, modified bump only on real change
      -> Result(changes, warnings, diff)
      -> cli: print diff / --json; if --write: apply(res) writes files
```
Handlers never write; they return a `Result`. The CLI is the only writer. `plan_write` compares the new text with the on-disk text and records no change when equal, which gives idempotence and the no-op `modified` guarantee. `modified` is set to max(now, previous + 1s) so it is strictly newer than the database row (Frappe imports only when the file is newer: `frappe/frappe/modules/import_file.py`).

## Byte format and style
`dump` mirrors `frappe.as_json` plus the exporter: indent 1, sorted keys, `ensure_ascii`, one trailing newline, nulls dropped, child default fields stripped (`frappe/frappe/__init__.py`). `detect_style` inspects an existing file and `render` reproduces it, so editing a hand-written file yields a minimal diff. New files get canonical format.

## Catalog cache
Location: `<app>/.fdesk-cache/catalog.json` (gitignored). Content: doctypes, reports, pages, workspaces, charts, number cards, blocks with their source file.
- Fingerprint = app git HEAD + path/mtime of scanned JSON files + frappe checkout path/version.
- Read path: load, validate schema and fingerprint; any failure (missing, truncated, bad schema, stale fingerprint) triggers a rebuild. A cache is never trusted unvalidated.
- Write path: temp file then atomic rename.
- `lookup(kind, name, app=None)` returns True/False, or None when the catalog cannot be built (no frappe checkout); callers treat None as a warning, not an error.
- Commands: `catalog refresh [--force]`, `status`, `search`.

## Icon index
Union of sprite ids from every `app_include_icons` sprite found (lucide, timeless, espresso, module-icons, app sprites) plus `aliases.json`. `exists(name, context)`: context `dock` additionally accepts `-duotone`; returns None when no sprite is found.

## Lint design
Each rule is a function `rule_Lxx(app, ctx) -> list[Finding]` registered in a table keyed by ID. `Finding` = id, severity (error, warn, info), file, message, fix hint. `ctx` holds the loaded entity graph (sidebars, docks, workspaces, cards, charts, blocks), the catalog, the icon index, and git HEAD texts. `lint` runs all rules or `--only`; `--strict` promotes warnings to errors. Exit 1 on any error. Rules never mutate files. Rule meanings are defined in `skills/frappe-desk/references/gotchas.md`.

## Adding a command
1. In the owning module add `def cmd_x(args) -> Result` using `App.find(args.app)`, `load`, mutate, `plan_write`.
2. Register in that module's `register(sub)`: `p = gs.add_parser("x"); p.add_argument(...); p.set_defaults(func=cmd_x)`.
3. Make it idempotent: identical request is a no-op; conflicting request raises `FdeskError`; soft issues go to `res.warnings`.
4. Tests in `tests/test_<module>.py`: happy path, idempotent re-run, conflict, no-op leaves `modified`, preserved style, one subprocess CLI test.
5. Add a row to `references/recipes.md` with its verify command.

## Adding a lint rule
1. Pick the next ID; add a row to the table in `references/gotchas.md` and a trap row naming the source path.
2. Implement `rule_Lxx` in `lint.py` and register it.
3. Test with a passing and a failing fixture tree; assert finding ID and file.
4. If a command can create the violation, make that command prevent it and add a regression test.

## Verification against a bench
`apply`: migrate, clear-cache, re-import changed standard rows. `verify`: reads DB rows (name, items, icon, `modified`) and compares with files; reports Custom Sidebar rows that override file changes. Both need `--bench` and `--site`; nothing else does.
