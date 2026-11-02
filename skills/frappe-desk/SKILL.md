---
name: frappe-desk
description: Change a Frappe v16 app's desk navigation deterministically with the bundled `fdesk` CLI - add, move, remove or rename Sidebar items and sections, change icons (sidebar, dock, workspace), edit the Dock, create Workspaces and their shortcuts, link cards, charts, number cards, quick lists and Custom HTML Blocks (frappe-ui styled), then lint, apply and verify on a bench. Use for any task touching a sidebar, dock, workspace, workspace shortcut, desk icon, number card, dashboard chart or custom block in a Frappe/ERPNext v16 app. Not for Frappe v15, DocType form layouts, or website/portal pages.
---

# frappe-desk

The agent decides the design. `scripts/fdesk` does the edits (byte-exact Frappe JSON, `modified` bumped only on real change), `fdesk lint` catches the sync traps, `fdesk apply`/`verify` prove it on a bench.

Frappe v16 only. v15 has no `Sidebar`/`Dock` doctypes: stop and say so.

## The loop (5 calls for a typical change)
```bash
F=<this skill>/scripts/fdesk          # python3, stdlib only, no install
$F --app <app path> catalog status    # 1. what exists (self-healing cache; rebuilds itself)
$F --app <app> <group> <cmd> ...      # 2. dry-run: prints the diff, writes nothing
$F --app <app> --write <group> <cmd> ...  # 3. apply the same command with --write
$F --app <app> lint --strict          # 4. rules L01-L16; exit 1 on error
$F --app <app> --write apply --bench <bench> --site <site>   # 5. migrate + clear-cache + reload, then:
$F --app <app> verify --bench <bench> --site <site>          #    DB rows == files
```
Every mutating command is idempotent: a second run says `no changes`. Add `--json` for machine output. `--app` is any path inside the app (default cwd).

## Task -> command (flags: `fdesk <group> <cmd> -h`; full recipes in `references/recipes.md`)
| Task | Command |
|---|---|
| Add a sidebar link | `sidebar add-item <sidebar> --section S --link-type DocType --to "Sales Order" --icon shopping-cart` |
| Move / rename / remove | `sidebar move-item`, `rename-item`, `remove-item` |
| New section (always starts with an Overview workspace link) | `sidebar add-section <sidebar> --label S --icon I --overview-workspace W` |
| Change an icon | `sidebar set-icon` (item, section, `--header`), `workspace set-icon`, `dock set-icon` |
| Dock tile (rail icon under the logo) | `dock add-tile --link-type Sidebar --to X --title T --icon I` |
| New workspace | `workspace new <name> --module M --icon I` |
| Workspace content | `workspace add-shortcut`, `add-link-card` + `add-link`, `add-chart`, `add-number-card`, `add-quick-list`, `add-header`, `move-block` |
| Chart / number card files | `chart new`, `number-card new` |
| Custom HTML block | `block new --kind stat\|kpi-row\|list\|progress\|empty\|links --name N --workspace W`, preview with `block render-preview` |
| Find an icon / a link target | `icons search Q`, `icons check NAME`, `catalog search Q --kind doctype\|report\|page\|workspace` |
| Undo an experiment | `snapshot --name X` before, `restore --name X` after |

## Hard rules (the CLI enforces them; know why)
1. **Never hand-edit nav JSON.** Folder and file names must equal `scrub(name)`; formatting is Frappe's (1-space indent, sorted keys, escaped non-ASCII, one trailing newline); hand-written files keep their own style. Use the CLI.
2. **A changed file imports on migrate only if its `modified` is newer than the DB row.** The CLI bumps it. If a site still shows the old version, `apply --reset-nav`.
3. **A Section Break cannot carry a link.** Each section starts with an Overview child; empty sections do not render.
4. **Icons come from sprites, not a free list.** Check with `icons check`; `-duotone` is valid only on Dock tiles.
5. **Number Cards and Dashboard Charts are not reaped** when their file is removed: renames and deletions need a patch (see `references/recipes.md` "Rename and removal").
6. **Custom HTML Blocks ship as fixtures** (`fixtures/custom_html_block.json` + a `hooks.py` fixtures filter), not as entity files. Scripts must use `root_element`; style with CSS variables so dark mode works.
7. **A Custom Sidebar row on a site can mask your change.** `verify` lists overriding rows.
8. **Never delete rows or migrate on a site without saying so.** `apply` and `restore` are dry-run unless `--write`; `--reset-nav` deletes only Sidebar/Dock rows that the files redefine.
9. Standard docs can be edited in the DB only in developer mode; file edits + migrate work everywhere.

## When something looks wrong
| Symptom | Look at |
|---|---|
| Item missing from the sidebar | role/permission on the linked doctype (L06/L14 hints), hidden flag, Custom Sidebar override (`verify`) |
| Generic list icon | icon id not in a sprite (`icons check`, L05) |
| Change not visible after migrate | `modified` not newer (L02), stale cache: `apply` clears it, hard-reload the browser |
| Workspace page blank/empty blocks | content block <-> child row label mismatch (L07) |
| Landing opens under the wrong sidebar | workspaces open in the sidebar of their module; one module = one sidebar |

Details for every trap: `references/gotchas.md`. How it works: `references/mechanics.md`. Icons: `references/icons.md`. Blocks: `references/custom-blocks.md`.

## Do not
- Do not guess a link target: `catalog search` first. A missing target is lint L06.
- Do not invent icon names; do not copy icons from another UI library.
- Do not run `bench migrate` by hand in place of `fdesk apply --write`: it skips the changed-file check and the cache/reload steps.
