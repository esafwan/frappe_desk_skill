# frappe-desk

Agent skill plus a deterministic CLI (`fdesk`) for changing a Frappe v16 app's desk navigation: Sidebar, Dock, Workspace, shortcuts, link cards, charts, number cards, icons, Custom HTML Blocks. Edits are made on the app's source JSON by a script, not by hand, so output is byte-stable and lint-checked.

## What it does
| Area | Commands |
|---|---|
| Sidebar | add/move/remove/rename item, sections, spacers, icons, new sidebar |
| Dock | add/remove tile, icon |
| Workspace | new, shortcuts, link cards, charts, number cards, quick lists, headers, custom blocks, roles, hide |
| Charts / cards | `chart new`, `number-card new` with per-type required fields |
| Icons / catalog | search and check icons; searchable cache of linkable doctypes, reports, pages |
| Quality | `lint` (L01-L16), `snapshot`/`restore`, `apply`, `verify` against a bench |

Reference docs: `skills/frappe-desk/references/` (`mechanics.md`, `recipes.md`, `gotchas.md`, `icons.md`, `custom-blocks.md`). Design: `docs/ARCHITECTURE.md`.

## Install as an agent skill
Copy or symlink the skill folder into your agent's skills directory:
```sh
# Claude Code (user scope)
ln -s "$(pwd)/skills/frappe-desk" ~/.claude/skills/frappe-desk
# or copy
cp -R skills/frappe-desk ~/.claude/skills/frappe-desk
```
Project scope: `.claude/skills/frappe-desk`. Other agents: place the folder where they read `SKILL.md` skills. No package install is needed; `scripts/fdesk` is a stdlib-only entry point.

## Quickstart
```sh
F="skills/frappe-desk/scripts/fdesk --app /path/to/your_app"
$F lint                                                   # 1. check current state
$F sidebar list "Selling"                                 # 2. inspect
$F icons search warehouse                                 # 3. pick an icon
$F sidebar add-item "Selling" --section Reports \
     --link-type Report --to "Sales Register" --icon sheet  # 4. dry run: prints a diff
$F --write sidebar add-item "Selling" --section Reports \
     --link-type Report --to "Sales Register" --icon sheet  # 5. apply, then: $F lint --strict
```

## Safety model
- Dry-run by default: every mutating command prints a unified diff; nothing is written without `--write`.
- Idempotent: repeating a command makes no change and does not bump `modified`.
- Existing file style is preserved; new files use Frappe's canonical export format.
- `fdesk snapshot` copies the navigation JSON set to `.fdesk-snapshots/`; `fdesk restore` reverts.
- Only files inside the target app are written. `apply`/`verify` need a bench; edits and lint do not.
- Exit codes: 0 ok, 1 lint error, 2 usage or validation error.

## Requirements
- Python >= 3.10, standard library only.
- Frappe v16 target app only. v15 and earlier use a different navigation model and are refused.
- A frappe checkout (any bench `apps/` dir) improves icon and catalog checks; without it those checks degrade to warnings.

## Tests
```sh
python -m pytest -q tests
```

## License
MIT.
