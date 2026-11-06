# URY dashboards in the desk: what they depend on

The 16 URY dashboards render inside the Frappe desk as **islands** (Tier B). Islands are not in a
released `version-16`. Two supported ways to get them:

1. **Frappe v16 + patch** (what the URY v16 apps run on): `version-16-hotfix` plus
   [frappe/frappe#45046](https://github.com/frappe/frappe/pull/45046) "feat: Insights dashboards in v16 desk"
   (open, base `version-16-hotfix`, head `e205163`). Patch file: `frappe_patches/0000-frappe-pr-45046-islands-backport-v16.patch`.
   Bench `sa-v16-islands` (v16-hotfix at `72a6fa9`, PR merged in) runs this way.
2. **Frappe `develop`** (17.0.0-dev): islands are native, no patch. Bench `sa-v17-islands`.

Either way Insights `develop` is required.

## Versions this was built and proven on (bench `sa-v17-islands`, 2026-11-03/06)

| Component | Version |
| --- | --- |
| Frappe | `develop` @ `c58b630` (17.0.0-dev, has `frappe/utils/island.py`) |
| Insights | `develop` @ `8352da5` (3.50.0-dev) |
| Apps on the site | `frappe`, `insights` only (URY tables copied in as plain tables; data source `ury_db`) |

Check before using another bench: `test -f apps/frappe/frappe/utils/island.py`, and
`import importlib.util as u; u.find_spec('frappe.utils.island')`. Both must succeed, else stay on Tier A.

## Frappe patches

Route 1 needs `0000-...pr-45046...patch` (apply to `version-16-hotfix`: `git apply --3way` or
`git fetch upstream pull/45046/head && git merge FETCH_HEAD`; do `git fetch --unshallow upstream` first).
Route 2 needs no code patch. On the `sa-v17-islands` bench the only Frappe change is a data file: a dock entry so the sidebar is reachable.

- `frappe_patches/0001-dock-add-ury-analytics.patch` adds an "Analytics" dock icon
  (`chart-column`) pointing to the Sidebar `URY Analytics` in `frappe/dock/frappe/frappe.json`.
- Apply from the frappe app dir: `git apply <this repo>/.../frappe_patches/0001-dock-add-ury-analytics.patch`,
  then `bench --site SITE migrate` (or `clear-cache`).

## Site data that is not in the dashboard specs

- Sidebar `URY Analytics` (header icon `chart-column`), 16 items, each `type Link`, `link_type Dashboard`,
  `link_to` the dashboard name (e.g. `ury-owner-overview`), icon `layout-dashboard`.
  Labels carry no `DBnn` prefix. These rows live only in the site database today.
- Workspace `URY Analytics`.
- Data source `ury_db` (see `design/islands_proof_report.md` in the track) and a completed setup wizard.

## Insights develop compatibility

finsights needed the shim in `references/develop-compat.md` (env `INSIGHTS_DATA_SOURCE`).
