# Gotchas

Each trap: symptom, cause (source), fix, lint ID. Lint IDs are defined here (canonical table); `fdesk lint --only L02,L05` runs a subset.

## Lint ID table

| ID | Meaning |
|---|---|
| L01 | File path equals `scrub(name)` folder and file |
| L02 | `modified` strictly greater than the value in the base (git HEAD) file for every changed entity |
| L03 | Section Break has no link and has an Overview child |
| L04 | Duplicate link (same identity) in one sidebar |
| L05 | Icon unknown (union of installed sprites; `-duotone` valid on Dock only) |
| L06 | Link target missing in the catalog |
| L07 | Workspace content block to child row mapping (all block types), unique ids, no orphan child rows |
| L08 | Number Card / Dashboard Chart file missing, or rename without a patch note |
| L09 | Workspace header keys and type rules |
| L10 | URL item uses `url`, not `link_to` |
| L11 | `is_default_module` conflicts |
| L12 | Custom block rules: `var(--)` tokens, `root_element`, no `document.querySelector`, no external requests, private or roles |
| L13 | Dock/landing integrity: no standard Dock, tile targets missing Sidebar/Workspace, module Sidebar without Workspace landing (warning) |
| L14 | `link_count` mismatch and child-row required fields |
| L15 | Number Card / Dashboard Chart required fields per type |
| L16 | Custom Sidebar override risk (info) |

## Traps

| # | Symptom | Cause (source) | Fix | Lint |
|---|---|---|---|---|
| 1 | Edited JSON, migrate changes nothing | Import skips when `file.modified <= db.modified` (`frappe/frappe/modules/import_file.py`) | Bump `modified` (CLI does it on real changes only), or `migrate --force` | L02 |
| 2 | Sidebar/Dock/Workspace vanishes after migrate | Folder or file name differs from `scrub(name)`; reaper deletes standard rows with no file (`frappe/frappe/model/sync.py` `remove_orphan_entities`) | Rename folder + file to `scrub(name)` | L01 |
| 3 | Section shows children but clicking the header navigates, or no Overview | Section Break carries a link, or no first child pointing at the workspace (`sidebar_item.js`) | Section Break: no `link_to`/`url`; add an Overview child | L03 |
| 4 | A link silently disappears | Items with the same identity `(type, link_type, link_to, url, filters)` collapse to the first (`frappe/frappe/desk/doctype/sidebar/sidebar.py` `LINKED_IDENTITY_FIELDS`) | Distinguish by `filters`, or remove the duplicate | L04 |
| 5 | Two spacers collapse into one | Unlinked rows are identified by hash of type+label (`sidebar.py`) | Give each spacer a distinct label | L04 |
| 6 | Empty box where an icon should be | Unknown name draws nothing (`frappe/frappe/public/js/frappe/utils/utils.js`) | `fdesk icons search`; bare name, no `icon-` prefix | L05 |
| 7 | Letter tile instead of icon | No icon set on Dock item or Sidebar (`dock.js`, `sidebar_header.js`) | Set `icon` / `header_icon` | L05, L13 |
| 8 | Legacy icon names (`sell`, `stock`) show nothing | Old vocabulary has no symbols | Use lucide or `-duotone` names | L05 |
| 9 | `-duotone` icon not found on a sidebar item | `-duotone`/`-soft` live in `module-icons.svg`, rendered as variants on the Dock | Use lucide or module-icon names on items and headers; `-duotone` variants are valid on Dock tiles only | L05 |
| 10 | Item shows but opens "not found" | `link_to` names a missing DocType/Report/Page/Workspace | Check with `fdesk catalog search`; fix name | L06 |
| 11 | Child rows have no icon | Children of an `indent=1` section never draw icons (`sidebar_item.js`) | Omit `icon` on them; nothing to fix | none |
| 12 | Child ends up in the wrong section | Nesting is order + `child=1`, there is no parent id (`sidebar.js` `nest_section_items`) | Use `move-item`, which places rows in order | L03 |
| 13 | Workspace shortcut exists but not shown (or block shows empty) | Content block `shortcut_name` not equal to a child `label`, duplicate block ids, or orphan child (`blocks/*.js`, `workspace.py`) | Add via CLI (writes both); fix label | L07 |
| 14 | Link card shows wrong count / rows hidden | `link_count` on Card Break differs from Link rows after it (`workspace.py` ~515) | Let CLI recompute | L14 |
| 15 | Workspace link save error | Child required fields missing: `type`, `label`, and `link_type`+`link_to` for Links (`workspace_link.json`) | Fill them | L14 |
| 16 | Workspace deleted on migrate | `public=1, standard=1` workspace whose file path is wrong (`sync.py`) | Fix path; keep `standard=1`, `public=1` | L01, L09 |
| 17 | Workspace import error | Missing `label`, `title`, `module` or `type` (`workspace.json`) | Fill required header keys; `type` is Workspace, Link or URL | L09 |
| 18 | URL item does nothing | URL item uses `link_to` instead of `url` (`sidebar_item.json`) | Set `url`, clear `link_to` | L10 |
| 19 | Module opens another module's sidebar | Two sidebars claim `is_default_module` for the same target (`sidebar.json`) | Only one app-shipped sidebar per target | L11 |
| 20 | Number card/chart renders nothing after migrate | `is_standard` or `module` missing; type-specific fields missing (`number_card.json`, `dashboard_chart.json`) | Fill required fields; migrate to run `sync_dashboards` | L15 |
| 21 | Card/chart file removed but still on site | Not in `ORPHANABLE_ENTITIES`; never reaped | Add a delete patch | L08 |
| 22 | Chart/number card name changed, workspace shows blank | Rename without patch/rewire | Patch + update workspace child rows | L08, L07 |
| 23 | Custom block not appearing on a fresh site | Not file-synced; needs `fixtures/custom_html_block.json` + `fixtures` hook (`frappe/frappe/utils/fixtures.py`) | Use `block new` (writes both); migrate | L12 |
| 24 | Custom block invisible to other users | Non-managers forced `private=1`; or `roles` exclude user (`custom_html_block.py`) | Ship as public with intended `roles` (as a manager) | L12 |
| 25 | Block script throws or hits another block | `document.querySelector` cannot see the shadow root (`dom.js`) | Use `root_element.querySelector` | L12 |
| 26 | Block colors wrong in dark mode | Hard-coded hex instead of tokens | Use `var(--...)` | L12 |
| 27 | File change has no effect on one site | A `Custom Sidebar` (site/user) row overrides label/icon or hides the item (`custom_sidebar.py`) | Delete or edit the override; `verify` lists them | L16 |
| 28 | Cannot edit a standard Sidebar in the UI | Allowed only in developer mode (`sidebar.py` `validate_app_content`) | Edit the file, or use Custom Sidebar | none |
| 29 | Sidebar save error on title | Title equals a Module Def name or contains `/ ? # % \` (`sidebar.py`) | Choose another title | L01 |
| 30 | Rail has no tile for the app | App has no standard Dock, or tile points to a missing Sidebar (`dock.py` `get_app_dock`) | `dock add-tile` | L13 |
| 31 | Changes not visible after successful migrate | Boot cache: `module_sidebars`, dock layers, `sidebar_computed_base` (`sidebar.py`) | `bench clear-cache` + hard reload | none |
| 32 | New shell shows a hammer icon and auto-generated items | Module has no Sidebar record; computed sidebar fallback (`sidebar.py` `DEFAULT_HEADER_ICON`) | Ship a Sidebar JSON | L13 |
| 33 | Edited `erpnext/.../workspace_sidebar/*.json`, nothing happens | Legacy archive, inert (`workspace_sidebar.py`) | Edit the `Sidebar` file | none |
| 34 | Diff touches every line of a file | Reformatted a hand-written file to canonical style | Use the CLI (preserves style); see byte format in `mechanics.md` | none |
| 35 | Workspace `sidebar_items` key ignored | `workspace.json` has no such field | Remove; use the Sidebar | none |

Where "none" appears, no lint rule can detect the condition from files alone; it is behavior to know.

## Added after live testing
| # | Symptom | Cause | Fix | Lint |
|---|---|---|---|---|
| 36 | An app's workspace/sidebar JSON file vanished after "resetting" a doc | `frappe.delete_doc` on a standard doc in developer mode deletes the exported file from disk; `Workspace.on_trash` also rewrites Custom Sidebar rows (`frappe/desk/doctype/workspace/workspace.py` `on_trash`) | Never delete standard nav docs with `delete_doc` to force a re-import. `fdesk apply` force-imports changed files (`import_file_by_path(force=True)`) and deletes nothing | none |
| 37 | Dock has no tiles after migrate although the file has them | Frappe imports Dock before Sidebar; `Dock.validate` drops rows it cannot anchor (`anchor_the_items` in `dock.py`) | `fdesk apply` force-imports changed files after migrate, Sidebar before Dock | none |
| 38 | Custom HTML Block placed on a workspace renders empty | The widget finds its child row by `label` == content `custom_block_name` (`views/workspace/blocks/block.js` `make`), so a pretty label breaks it | Keep `custom_blocks[].label` equal to the block name (`fdesk block new` does); lint L07 | L07 |
| 39 | `verify` passes though a file was deleted | Verify walks files on disk; a deleted file is not checked | Run `git status` on the app before and after `apply`; deletions show as `D` | none |
