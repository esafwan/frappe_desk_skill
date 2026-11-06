# URY dashboards in the desk: what they depend on

The 16 URY dashboards render inside the Frappe desk as **islands** (Tier B). This needs
Frappe `develop` (17.0.0-dev); islands are **not** in `version-16`.

## Versions this was built and proven on (bench `sa-v17-islands`, 2026-11-03/06)

| Component | Version |
| --- | --- |
| Frappe | `develop` @ `c58b630` (17.0.0-dev, has `frappe/utils/island.py`) |
| Insights | `develop` @ `8352da5` (3.50.0-dev) |
| Apps on the site | `frappe`, `insights` only (URY tables copied in as plain tables; data source `ury_db`) |

Check before using another bench: `test -f apps/frappe/frappe/utils/island.py`, and
`import importlib.util as u; u.find_spec('frappe.utils.island')`. Both must succeed, else stay on Tier A.

## Frappe patch

There is **no patch to Frappe code**. Islands are native in `develop`. The one change made in the
Frappe checkout is a data file: a dock entry so the sidebar is reachable.

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
