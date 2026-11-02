# Icons

Verified against frappe `version-16`.

## Rules
| Rule | Detail | Source |
|---|---|---|
| Bare name | store `factory-duotone`, never `icon-factory-duotone` | `frappe/frappe/public/js/frappe/utils/utils.js` `frappe.utils.icon` |
| Emoji | valid (field options `Emojis`); rendered as a span | same |
| `es-` prefix | Espresso set, rendered as `#es-...` | same |
| Unknown name | draws an empty box, no error | same (`<use href="#icon-x">`) |
| Missing icon | letter tile from `A..Z` sprite (sidebar header, Dock item) | `frappe/frappe/public/js/frappe/ui/sidebar/sidebar_header.js`, `dock.js` |
| Computed sidebar fallback | `hammer` when the module's Workspace has no icon | `frappe/frappe/desk/doctype/sidebar/sidebar.py` (`DEFAULT_HEADER_ICON`) |
| Legacy names | old vocabulary (`sell`, `stock`, `organization`, `home`) has no symbol | `erpnext/erpnext/workspace_sidebar/*.json` vs sprites |
| Renamed lucide | `aliases.json` maps old to new names (`trash-2` to `trash`) | `frappe/frappe/public/icons/lucide/aliases.json` |

## Where icons are set
| Place | Field | Convention |
|---|---|---|
| Sidebar header | `Sidebar.header_icon` | `-duotone` variant |
| Sidebar item / section | `Sidebar Item.icon` | lucide name; omit on children of an `indent=1` section (not drawn) |
| Dock tile | `Dock Item.icon` | `-soft` variant of the same glyph; `-duotone` is valid only on Dock items |
| Workspace | `Workspace.icon` | lucide name; also becomes the computed sidebar icon |

Convention source: `erpnext/erpnext/dock/erpnext/erpnext.json` (`-soft`), `erpnext/erpnext/*/sidebar/*/*.json` (`-duotone`); `dock.js` adds class `dock-item--duotone` for `-duotone` names.

## Sprites (union of all installed apps)
| File | Ids | Note |
|---|---|---|
| `frappe/frappe/public/icons/lucide/icons.svg` | `icon-<lucide-name>` | main set (~1.8k) |
| `frappe/frappe/public/icons/timeless/icons.svg` | `icon-<name>` | legacy |
| `frappe/frappe/public/icons/espresso/icons.svg` | `es-line-*`, `es-solid-*` | name must start `es-` |
| `frappe/frappe/public/icons/module-icons.svg`, `erpnext/erpnext/public/icons/module-icons.svg` | `icon-<x>-duotone`, `icon-<x>-soft` | module glyphs |

Sprites come from every installed app's `app_include_icons` hook (`frappe/frappe/utils/jinja_globals.py`). An app can ship its own sprite: `app_include_icons = ["/assets/<app>/icons/<file>.svg"]`, each `<symbol id="icon-<name>">`.

## fdesk
- `fdesk icons search Q`, `fdesk icons check NAME --context sidebar|workspace|dock`, `fdesk icons list --prefix P`.
- `check` returns exists / missing / unknown (unknown = no frappe checkout found). Lint L05 uses the same check.
- Pick the glyph first with `icons search`; never invent a name.
