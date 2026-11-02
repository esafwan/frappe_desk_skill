# Custom HTML Blocks

Verified against frappe `version-16`.

## Runtime model
| Fact | Source |
|---|---|
| Rendered by `frappe.create_shadow_element(wrapper, html, css, js)` into an open shadow root | `frappe/frappe/public/js/frappe/dom.js` |
| `desk.bundle.css` is linked inside the shadow root; `<script>` and `<style>` tags in `html` are stripped | `dom.js` (`remove_script_and_style`) |
| `style` is appended as a `<style>`; `script` runs inside an IIFE with `root_element` = the shadow root | `dom.js` |
| Each block is its own custom element `custom-block-<random>` | `dom.js` |
| Block doc fetched with `frappe.model.with_doc("Custom HTML Block", name)` | `frappe/frappe/public/js/frappe/widgets/custom_block_widget.js` |
| Non-managers are forced to `private=1`; list shows public blocks plus own private ones | `frappe/frappe/desk/doctype/custom_html_block/custom_html_block.py` |
| Workspace needs a `custom_blocks` child `{custom_block_name, label}` and a `custom_block` content block | `workspace.py`, `blocks/custom_block.js` |
| Not file-synced; ship as fixture `<app>/fixtures/custom_html_block.json` + `fixtures` filter in `hooks.py` | `frappe/frappe/utils/fixtures.py` |

## Rules (lint L12)
| Rule | Why |
|---|---|
| Colors, spacing, radius, fonts only via `var(--token)`; no hex literals | light/dark theme follows `data-theme-mode` on `<html>`; custom properties inherit across the shadow boundary |
| Query only through `root_element` (`root_element.querySelector`) | `document.querySelector` cannot see into the shadow root and may hit other blocks |
| No `fetch`/XHR to external hosts, no external `<script src>`, `<link>`, `@import url(http...)` | data stays on the site; offline/CSP safe |
| Same-site data via `frappe.call` / `frappe.db.get_list` is allowed | respects user permissions |
| Set `private=1` or restrict `roles` | public blocks are visible to every desk user |
| Guard every async result: loading, empty, error | see data states |
| `script` wrapped by Frappe in an IIFE: do not leak globals, no `export` | `dom.js` |

## Tokens
Use Frappe's desk variables, for example: `--text-color`, `--text-muted`, `--text-light`, `--heading-color`, `--bg-color`, `--card-bg`, `--control-bg`, `--border-color`, `--primary`, `--green-500` (status), `--red-500`, `--orange-500`, `--border-radius`, `--border-radius-md`, `--padding-md`, `--margin-md`, `--shadow-sm`. Confirm a token exists in the target frappe before relying on it: `rg -- "--card-bg" frappe/frappe/public/scss`. Declare a fallback only if you must: `var(--card-bg, transparent)`.

## Accessibility
| Item | Rule |
|---|---|
| Structure | real elements: `<section aria-label>`, `<ul>`, `<table>`, `<button>`, `<a href>` |
| Names | every interactive element has visible text or `aria-label` |
| Status | never color alone; add text or an icon |
| Live data | container `aria-live="polite"` for values that refresh |
| Focus | keep default outline or a `:focus-visible` ring using `--primary` |
| Contrast | use text tokens, not custom grays |
| Motion | wrap animation in `@media (prefers-reduced-motion: no-preference)` |

## Dark mode
Tokens switch automatically. Test both: toggle theme in the user menu, or `document.documentElement.setAttribute("data-theme-mode","dark")`. `fdesk block render-preview --name N` writes a standalone page with light/dark toggle and default token values. Do not use `prefers-color-scheme`; the desk theme setting is the authority (`frappe/frappe/public/js/frappe/ui/theme_switcher.js`).

## Data states
| State | Render |
|---|---|
| Loading | skeleton or "Loading" text, `aria-busy="true"` |
| Empty | one sentence + a link to the list/new form |
| Error | short message, no stack trace; keep the rest of the workspace usable |
| Value | number formatted with `frappe.format`/`toLocaleString`, not string concatenation |

## Templates and commands
Kinds: `stat`, `kpi-row`, `list`, `progress`, `empty`, `links`. `fdesk block new --kind K --name N --workspace W` writes the fixture record, the hooks entry, the workspace child row and content block. `fdesk block list`, `fdesk block remove`. Lint: L12 (block rules), L07 (workspace wiring).
