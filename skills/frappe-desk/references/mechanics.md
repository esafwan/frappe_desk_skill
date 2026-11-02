# Frappe v16 desk navigation: mechanics

Verified against frappe `version-16` (16.52.0) and erpnext `version-16`. Paths are relative to a bench `apps/` directory. v16 only: the `Workspace Sidebar` model is an inert archive in this build (`frappe/frappe/desk/doctype/workspace_sidebar/workspace_sidebar.py`; listed for deletion in `frappe/frappe/desk/RETIRING.md`). Author `Sidebar` and `Dock` files. Files under `erpnext/erpnext/workspace_sidebar/` do nothing at runtime.

## 1. Entities

| Entity | Role | Name / file | Source |
|---|---|---|---|
| Sidebar | Module shell: ordered item list + header icon | name = `title`; `<module>/sidebar/<scrub(title)>/<scrub(title)>.json` or app-rooted `<app>/sidebar/...` | `frappe/frappe/desk/doctype/sidebar/sidebar.json`, `frappe/frappe/model/sync.py` |
| Sidebar Item | Child row of Sidebar | n/a | `frappe/frappe/desk/doctype/sidebar_item/sidebar_item.json` |
| Dock / Dock Item | App rail: one tile per sidebar/workspace/URL | name = app; `<app>/dock/<app>/<app>.json` | `frappe/frappe/desk/doctype/dock/dock.json`, `erpnext/erpnext/dock/erpnext/erpnext.json` |
| Workspace | Landing page: shortcuts, link cards, charts, blocks | name = `label`; `<module>/workspace/<scrub(name)>/<scrub(name)>.json` | `frappe/frappe/desk/doctype/workspace/workspace.json` |
| Number Card | KPI widget | `<module>/number_card/<scrub(name)>/...json` | `frappe/frappe/utils/dashboard.py` (`make_records_in_module`) |
| Dashboard Chart | Chart widget | `<module>/dashboard_chart/<scrub(name)>/...json` | same |
| Custom HTML Block | Free HTML/CSS/JS widget | no module, no file sync; ship as a fixture | `frappe/frappe/desk/doctype/custom_html_block/custom_html_block.json` |
| Custom Sidebar | Site/user override layer over a module Sidebar | DB only | `frappe/frappe/desk/doctype/custom_sidebar/custom_sidebar.py` |

Folder names follow doctype folder names (`dashboard_chart`, `number_card`). Folder and file name must equal `frappe.scrub(name)`.

## 2. Sidebar

Fields: `title` (record name; must not equal a Module Def name, must not contain `/ ? # % \`), `module` (Module Def, blank = app-rooted), `app` (derived from module, author-set when module blank), `header_icon`, `items`, `standard`. `frappe/frappe/desk/doctype/sidebar/sidebar.json`, `sidebar.py`.

`Sidebar Item` (`sidebar_item.json`):

| Field | Values / meaning |
|---|---|
| `type` | `Link`, `Section Break`, `Spacer` (`Sidebar Item Group` is not valid) |
| `link_type` | `DocType`, `Page`, `Report`, `Workspace`, `Dashboard`, `URL` (Link rows only) |
| `link_to` | target name (Dynamic Link on `link_type`); URL rows use `url` and `open_in_new_tab` instead |
| `icon` | shown on Link and Section Break rows |
| `child` | 1 = nested under the most recent Section Break (no parent id exists; nesting is order + `child=1`) |
| `indent`, `collapsible`, `keep_closed`, `show_arrow` | Section Break presentation flags |
| `filters`, `route_options`, `navigate_to_tab` | narrow the DocType list / select a form tab |
| `is_default_module` | this module is the target's default home; app-shipped sidebars only |
| `hidden`, `added`, `key` | meaningful only inside a Custom Sidebar |

Renderer rules (`frappe/frappe/public/js/frappe/ui/sidebar/sidebar.js` `nest_section_items`, `sidebar_item.js`):
- A Section Break becomes the current section; later `child=1` rows nest under it; `child=0` rows are top level.
- Children of an `indent=1` section draw no icon even if set (`hide_icon = item.child && item.parent?.indent`), so such rows normally carry no `icon`.
- `collapsible=1` draws a toggle; `collapsible=0` with `show_arrow=1` draws a leading arrow; both 0 is static.
- `keep_closed=1` starts collapsed unless the user's saved state in localStorage overrides it.
- A Link with no resolvable route is skipped. A shell with no openable item is dropped (Section Break and Spacer do not count).
- Item identity = `(type, link_type, link_to, url, filters)` (`LINKED_IDENTITY_FIELDS`, `sidebar.py`); label is not part of it. Duplicates are not rejected, the first is kept. Unlinked rows (Section Break, Spacer) are identified by a hash of type + label, so two spacers need distinct labels.
- `Page` links use the Page name, `Report` the Report name, `Dashboard` the Dashboard name.

Editing guard: `validate_app_content` allows editing a standard Sidebar only in developer mode or during import/fixtures/migrate/install/patch (`sidebar.py`). In developer mode `on_update` re-exports the JSON to the app.

## 3. How entities bind (no foreign key)

| Binding | Rule | Source |
|---|---|---|
| Sidebar to Workspace | same Module Def name in `Sidebar.module` and `Workspace.module`; plus an explicit Link item `link_type=Workspace` | `sidebar.py` `resolve_sidebar` |
| Sidebar to app | `Sidebar.app`, else `get_module_placement(module)` (app that lists the module in `modules.txt`, else `Module Def.app_name`) | `frappe/frappe/utils/modules.py` |
| Rail tile | the app's Dock has `{link_type: Sidebar, link_to: <Sidebar name>}`; no Dock file = no tile | `frappe/frappe/desk/doctype/dock/dock.py` `get_app_dock` |
| Shell URL | `/desk/<shell_slug>/<entity>`; slug = lowercase, spaces to `-`, `&` to ` and ` | `sidebar.py` |
| Module-rooted vs app-rooted | module-rooted lives under a module folder (`IMPORTABLE_DOCTYPES`); app-rooted lives at the app package root (`APP_ROOTED_DOCTYPES = [("desk","dock"),("desk","sidebar")]`) and needs `module` blank + `app` set | `frappe/frappe/model/sync.py` |
| No Sidebar for a module | a computed sidebar is generated; header icon = icon of the Workspace named like the module, else `DEFAULT_HEADER_ICON = "hammer"`; shell flagged `computed` | `sidebar.py` |

Worked example: a restaurant-ERP app with one module, no Sidebar file and no Dock file gets a computed sidebar (icon taken from its Workspace) and no rail tile; adding one module-rooted Sidebar JSON plus one app-rooted Dock JSON gives it a real shell and tile.

## 4. Dock

`Dock`: `app` (required), `standard`, `user` (blank for app/site layers), `mount_on`, `items`. `Dock Item`: `link_type` (`Sidebar`, `Workspace`, `URL`), `link_to`, `url`, `icon`, `title`, `hidden`, `added`. Three layers: app (`standard=1`), site, user. `hidden=1` removes the tile from the rail while keeping the sidebar routable. A module absent from the Dock still has a sidebar and routes. `Dock.autoname` uses the app name when `standard`. Source: `dock.json`, `dock.py`.

## 5. Custom Sidebar layer (can mask file changes)

Site-wide (`user` blank) or per-user rows over a module Sidebar. `added=0` rows reference a base item and override only `label` and `icon`; `added=1` adds an item; `hidden` hides. Resolution order: permission filter, customizations (later layer wins), private workspaces. Consequence: a file edit to a base item can appear to have no effect on a site that has a Custom Sidebar row for it. Source: `custom_sidebar.py`, `sidebar.py` `resolve_sidebar`. Also, the base is cached (`COMPUTED_BASE_CACHE_KEY = "sidebar_computed_base"`, `sidebar.py`).

## 6. Workspace

Required fields: `label`, `title`, `module`, `type` (`Workspace`, `Link`, `URL`) (`workspace.json`). Others: `icon`, `public`, `standard`, `parent_page`, `sequence_id`, `roles`, `is_hidden`, `hide_custom`, `indicator_color` (`green cyan blue orange yellow gray grey red pink darkgrey purple light-blue`), `module_onboarding`.

Child tables: `shortcuts` (Workspace Shortcut), `links` (Workspace Link), `charts`, `number_cards`, `quick_lists`, `custom_blocks`.
- Workspace Shortcut: `type` and `label` required; `type` in `DocType Report Page Dashboard URL`; `doc_view` in `List, Report Builder, Dashboard, Tree, New, Calendar, Kanban, Image`; URL type needs `url`.
- Workspace Link: `type` and `label` required; `type` is `Link` or `Card Break`; `Link` needs `link_type` + `link_to`; `link_count` on a Card Break is the number of Link rows after it (`workspace.py` ~line 515-522 recomputes only when 0).
- Workspace with `type=Link` needs `link_type`+`link_to`; `type=URL` needs `external_link`.

### Content blocks
`Workspace.content` is a Long Text holding a JSON string (array of `{id, type, data}`). Block types registered in `frappe/frappe/public/js/frappe/views/workspace/blocks/index.js`: `header`, `paragraph`, `card`, `chart`, `shortcut`, `spacer`, `onboarding`, `quick_list`, `number_card`, `custom_block`. Each block (except text/spacer) names a child row by label:

| Block type | `data` key | Resolves against |
|---|---|---|
| shortcut | `shortcut_name` | `shortcuts.label` |
| card | `card_name` | `links` Card Break `label` |
| chart | `chart_name` | `charts.label` |
| number_card | `number_card_name` | `number_cards.label` |
| quick_list | `quick_list_name` | `quick_lists.label` |
| custom_block | `custom_block_name` | `custom_blocks.custom_block_name` |
| onboarding | `onboarding_name` | `module_onboarding` |

Every block has a unique `id` and `data.col` (1-12). A child row with no block is invisible; a block with no child row renders nothing. Source: blocks `*.js` (`data.<x>_name`), `workspace.py`.

### Number Card and Dashboard Chart
Not reaped (not in `ORPHANABLE_ENTITIES`); synced by `sync_dashboards` on migrate (`frappe/frappe/migrate.py`, `frappe/frappe/utils/dashboard.py`). `is_standard=1` + `module` required to be file-backed. Required fields by type:

| Entity | Type | Required |
|---|---|---|
| Number Card | all | `label` |
| Number Card | Document Type | `document_type`, `function`; `aggregate_function_based_on` when function is not Count |
| Number Card | Report | `report_name`, `report_field`, `report_function` |
| Number Card | Custom | `method` |
| Dashboard Chart | all | `chart_name`, `filters_json` (at least `"[]"`) |
| Dashboard Chart | Count/Sum/Average | `document_type`, `based_on`; `value_based_on` for Sum/Average |
| Dashboard Chart | Group By | `document_type`, `group_by_based_on` (+ `aggregate_function_based_on` for Sum/Average) |
| Dashboard Chart | Report | `report_name` |

Sources: `number_card.json`, `dashboard_chart.json` (`mandatory_depends_on`). Chart `chart_type`: `Count Sum Average Group By Custom Report`; `type`: `Line Bar Percentage Pie Donut Heatmap`; `timespan`: `Last Year/Quarter/Month/Week`, `Select Date Range`.

### Custom HTML Block
Fields: `html`, `script`, `style`, `roles`, `private` (no module, no `standard`). Non-managers are forced to `private=1` (`custom_html_block.py` `validate_private`). Workspace references it via `custom_blocks` child `{custom_block_name, label}` plus a `custom_block` content block. Rendering: `frappe.create_shadow_element` (`frappe/frappe/public/js/frappe/dom.js`) builds a custom element with an open shadow root, links `desk.bundle.css` into it, strips `<script>/<style>` from `html`, appends `style`, and runs `script` in an IIFE where `root_element` is the shadow root. Details: `custom-blocks.md`.

## 7. Icons
Stored value is a bare name (no `icon-` prefix); emoji also valid (field options `Emojis`). `frappe.utils.icon()` (`frappe/frappe/public/js/frappe/utils/utils.js`): emoji to span; `es-` prefix to `#es-...`; otherwise `#icon-<name>` from `#all-symbols`. An unknown name renders an empty box; no icon renders a letter tile. See `icons.md`.

## 8. Sync on migrate

`run_schema_updates` (`frappe/frappe/migrate.py`): pre-model-sync patches, `sync_all()`, post-model-sync patches; then `sync_fixtures()`, `sync_dashboards()`, customizations, `after_migrate` hooks.

| Rule | Detail | Source |
|---|---|---|
| Discovery | `get_doc_files` globs `<module folder>/<doctype folder>/<name>/<name>.json`; app-rooted walk adds `<app>/dock/` and `<app>/sidebar/` | `frappe/frappe/model/sync.py` |
| Modified rule | `import_file_by_path(..., ignore_version=True)` skips the file when `file.modified <= db.modified`; it must be strictly newer (or run `migrate --force`) | `frappe/frappe/modules/import_file.py` (`is_db_timestamp_latest`) |
| Reaper | `ORPHANABLE_ENTITIES` = Workspace, Dashboard, Page, Report, Notification, Sidebar, Dock, List Filter, Kanban Board. Filters: Workspace `public=1,standard=1`; Sidebar `standard=1`; Dock `standard=1`. A row whose file path is missing is deleted | `sync.py` `remove_orphan_entities` |
| Dashboards | Number Card, Dashboard Chart, Dashboard are imported by `sync_dashboards` from `<module>/number_card`, `dashboard_chart`; never reaped | `frappe/frappe/utils/dashboard.py` |
| Custom HTML Block | not file-synced; ships via `<app>/fixtures/custom_html_block.json` + a `fixtures` entry in `hooks.py`, imported by `sync_fixtures` | `frappe/frappe/utils/fixtures.py` |
| Caches | sidebars ride in boot (`frappe.boot.module_sidebars`, dock layers): `bench clear-cache` + hard reload after migrate | `sync.py`, `sidebar.py` |
| Deletion | removing a file deletes standard Sidebar/Dock/Workspace rows at next migrate; Number Card/Chart/Custom HTML Block rows survive and need a patch | `sync.py` |

## 9. Export byte format

Frappe export = `frappe.as_json` (`frappe/frappe/__init__.py`): `json.dumps(obj, indent=1, sort_keys=True, separators=(",", ": "), ensure_ascii=True)` plus one trailing newline (written by the exporter). Consequences:

| Property | Value |
|---|---|
| Indent | 1 space (not tabs, not 2) |
| Key order | sorted, at every level |
| Non-ASCII | escaped as `\uXXXX` |
| Trailing newline | exactly one |
| Nulls | dropped on export |
| Child rows | default fields dropped (`name, parent, parenttype, parentfield, idx, doctype, owner, creation, modified, modified_by, docstatus`) |
| `content` | stays a JSON string, not an object |

Hand-written files may differ (tabs, explicit zero-valued defaults, compact rows). The CLI preserves a file's existing style on edit and only uses canonical format for new files, so diffs stay minimal. Do not reformat existing files wholesale.

## 10. Note on the source research
The upstream research notes say custom block CSS does not inherit desk CSS. Source says otherwise: `create_shadow_element` links `desk.bundle.css` inside the shadow root (`dom.js`), and CSS custom properties also inherit through the shadow boundary. Prefer `var(--...)` tokens regardless (see `custom-blocks.md`).
