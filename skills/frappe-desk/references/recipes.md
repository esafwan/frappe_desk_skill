# Recipes

Global flags go before the group: `fdesk --app PATH [--write] [--json] <group> <cmd> ...`. Default is dry-run (prints a diff). Add `--write` to apply. Every mutating command is idempotent: a second run changes nothing and does not bump `modified`. `<sidebar>` is the Sidebar name (its title). Run `fdesk lint` after every edit; see "After editing".

Convention below: `F="fdesk --app ."`.

## Sidebar

| Task | Command | Verify |
|---|---|---|
| Add an item | `$F --write sidebar add-item "Selling" --section "Reports" --link-type Report --to "Sales Register" --label "Sales Register" --icon sheet --after "Gross Profit"` | `$F sidebar list "Selling"` |
| Add a top-level DocType item (omit `--section`) | `$F --write sidebar add-item "Selling" --link-type DocType --to Customer --icon user-round` | `$F lint --only L04,L06` |
| Add a URL item | `$F --write sidebar add-item "Selling" --section "Tools" --link-type URL --url https://example.com/help --label Help --open-in-new-tab` | `$F lint --only L10` |
| Add a filtered link (same doctype twice) | `$F --write sidebar add-item "Selling" --section "Returns" --link-type DocType --to "Sales Invoice" --label "Credit Notes" --filters '{"is_return":1}'` | `$F lint --only L04` (filters make identity distinct) |
| Move an item | `$F --write sidebar move-item "Selling" --label Quotation --section "Reports" --after "Sales Register"` | `$F sidebar list "Selling"` |
| Remove an item | `$F --write sidebar remove-item "Selling" --label Quotation` | `$F lint` |
| Rename an item (label only) | `$F --write sidebar rename-item "Selling" --label Quotation --new-label Quote` | `$F sidebar list "Selling"` |
| Add a spacer | `$F --write sidebar add-spacer "Selling" --section Reports --after "Sales Register"` | `$F sidebar list "Selling"` |
| Add a hidden item | `$F --write sidebar add-item ... --hidden` (meaningful mainly in Custom Sidebar; prefer `remove-item`) | `$F sidebar list "Selling"` |
| Reorder sections | `$F --write sidebar reorder-sections "Selling" --order "Reports,Items,Setup"` | `$F sidebar list "Selling"` |
| Add a section (with Overview) | `$F --write sidebar add-section "Selling" --label "Returns" --icon undo-2 --after Reports --overview-workspace "Selling"` | `$F lint --only L03` |
| Remove a section | `$F --write sidebar remove-section "Selling" --label Returns` (add `--force` to drop its children too) | `$F lint --only L03,L04` |
| New sidebar (module-rooted) | `$F --write sidebar new "Kitchen" --module Kitchen --icon utensils` | `$F lint --only L01,L11` |
| New sidebar (app-rooted) | `$F --write sidebar new "Kitchen" --icon utensils --app-rooted` | `$F lint --only L01` |

Notes: an Overview child is the first Link under a Section Break pointing at the section's workspace (`--overview-workspace`); L03 checks it. Do not rename the Sidebar title through a file edit: see "Rename and removal".

## Icons

| Task | Command | Verify |
|---|---|---|
| Find a glyph | `$F icons search warehouse` | `$F icons check warehouse` |
| Item icon | `$F --write sidebar set-icon "Selling" --label Customer --icon user-round` | `$F lint --only L05` |
| Section icon | `$F --write sidebar set-icon "Selling" --label Reports --section Reports --icon sheet` | `$F lint --only L05` |
| Sidebar header icon | `$F --write sidebar set-icon "Selling" --header --icon storefront-duotone` | `$F icons check storefront-duotone --context sidebar` |
| Workspace icon | `$F --write workspace set-icon "Selling" --icon store` | `$F icons check store --context workspace` |
| Dock tile icon | `$F --write dock set-icon --title Selling --icon storefront-soft` | `$F icons check storefront-soft --context dock` |

## Dock

| Task | Command | Verify |
|---|---|---|
| Add a tile for a sidebar | `$F --write dock add-tile --link-type Sidebar --to Kitchen --title Kitchen --icon utensils --after Selling` | `$F dock list`, `$F lint --only L13` |
| Add a tile for a workspace / URL | `$F --write dock add-tile --link-type URL --url https://example.com --title Docs --icon book-open` | `$F lint --only L13` |
| Remove a tile | `$F --write dock remove-tile --title Kitchen` | `$F lint --only L13` |

One standard Dock per app, at `<app>/dock/<app>/<app>.json`; the first `add-tile` creates it.

## Workspace

| Task | Command | Verify |
|---|---|---|
| New workspace | `$F --write workspace new "Kitchen" --module Kitchen --icon utensils --label Kitchen` | `$F lint --only L01,L09` |
| Shortcut (DocType) | `$F --write workspace add-shortcut Kitchen --type DocType --to "Kitchen Order" --label "Kitchen Orders" --doc-view List --color Blue` | `$F lint --only L07` |
| Shortcut (URL) | `$F --write workspace add-shortcut Kitchen --type URL --url https://example.com --label Docs` | `$F lint --only L07,L10` |
| Remove shortcut | `$F --write workspace remove-shortcut Kitchen --label "Kitchen Orders"` | `$F lint --only L07` |
| Link card | `$F --write workspace add-link-card Kitchen --label Reports --icon sheet` then `$F --write workspace add-link Kitchen --card Reports --link-type Report --to "Kitchen Summary" --label Summary` | `$F lint --only L07,L14` |
| Chart | `$F --write workspace add-chart Kitchen --chart "Orders by Day" --col 6` | `$F lint --only L07,L08` |
| Number card | `$F --write workspace add-number-card Kitchen --card "Open Orders" --col 4` | `$F lint --only L07,L08` |
| Quick list | `$F --write workspace add-quick-list Kitchen --doctype "Kitchen Order" --label "Latest Orders"` | `$F lint --only L07` |
| Header / paragraph / spacer | `workspace add-header Kitchen --text "Today"`, `workspace add-paragraph Kitchen --text "..."`, `workspace add-spacer Kitchen` | `$F workspace list Kitchen` |
| Reorder blocks | `$F --write workspace move-block Kitchen --id ID --after ID` (ids from `workspace list`) | `$F lint --only L07` |
| Custom block | `$F --write block new --kind stat --name kitchen_stat --workspace Kitchen --title "Open Orders" --col 6 --roles "Kitchen Manager"` | `$F lint --only L07,L12` |
| Hide a workspace | `$F --write workspace hide Kitchen` (undo: `--unhide`) | `$F workspace list Kitchen` |
| Restrict by role | `$F --write workspace set-roles Kitchen --roles "Kitchen Manager,System Manager"` | `$F workspace list Kitchen` |

A shortcut/chart/number-card/block command adds both the child row and the content block, so L07 stays clean. Use the CLI, not hand edits of `content`.

## Charts and number cards

| Task | Command | Verify |
|---|---|---|
| Count number card | `$F --write number-card new "Open Orders" --module Kitchen --type "Document Type" --document-type "Kitchen Order" --function Count --filters '[["Kitchen Order","status","=","Open"]]'` | `$F lint --only L15` |
| Sum number card | `... --function Sum --aggregate-field grand_total` | `$F lint --only L15` |
| Report-based card | `$F --write number-card new "Late" --module Kitchen --type Report --report "Kitchen Summary" --report-field late --report-function Sum` | `$F lint --only L15` |
| Chart (count by day) | `$F --write chart new "Orders by Day" --module Kitchen --chart-type Count --document-type "Kitchen Order" --based-on creation --timeseries --time-interval Daily --timespan "Last Month" --type Line` | `$F lint --only L15` |
| Chart (group by) | `$F --write chart new "By Status" --module Kitchen --chart-type "Group By" --document-type "Kitchen Order" --group-by status --type Donut` | `$F lint --only L15` |

Then attach to a workspace with `add-chart` / `add-number-card`. Charts and cards need a migrate to land (`sync_dashboards`).

## After editing

1. `fdesk lint --strict`: fix every error.
2. `fdesk snapshot` before a risky batch (restore with `fdesk restore`).
3. Apply to a bench: `fdesk apply --bench BENCH --site SITE` (runs migrate, clear-cache, re-imports changed rows).
4. `fdesk verify --bench BENCH --site SITE`: DB rows must equal files (name, items, icon, `modified`); it also reports Custom Sidebar rows that override file changes.
5. Hard-reload the browser; sidebars ride in boot. If still stale: `bench --site SITE clear-cache`, restart web workers.
6. Commit the changed JSON files (and `fixtures/` + `hooks.py` for custom blocks).

Manual equivalent: `bench --site SITE migrate && bench --site SITE clear-cache`.

## Rename and removal (patch needed)

| Change | Procedure |
|---|---|
| Rename a Sidebar | Folder + file + `name` + `title` must all change together (scrubbed). Old standard row is reaped, new row is created (`frappe/frappe/model/sync.py`). Update Dock tiles (`dock set-icon`/`remove-tile` + `add-tile`) and any Workspace/Sidebar links to the old name. Add a patch only if site data (Custom Sidebar rows, user settings) references the old name. Lint: L01, L13 |
| Remove a Sidebar / Dock / Workspace | Delete the folder. The reaper deletes the `standard=1` row on next migrate. Remove Dock tiles and links to it first (L13, L06) |
| Rename or remove a Number Card / Dashboard Chart | Rows are not reaped. Add a patch in `<app>/patches.txt` that `frappe.delete_doc("Number Card", name, force=1, ignore_missing=True)` (or `frappe.rename_doc`). Update workspace child rows + content blocks. Lint: L08 warns when a rename lacks a patch note |
| Remove a Custom HTML Block | `fdesk block remove --name N`, plus a patch deleting the DB record (fixtures only add/update) |
| Rename a sidebar item label | `rename-item`; no patch needed (label is not identity), but Custom Sidebar rows keyed on it may orphan |
| Rename a Workspace | Same as Sidebar: folder/file/`name`/`label`/`title`, sidebar `Workspace` links, Dock tiles |

Patch skeleton:
```python
import frappe

def execute():
	frappe.delete_doc("Number Card", "Old Name", force=1, ignore_missing=True)
```
