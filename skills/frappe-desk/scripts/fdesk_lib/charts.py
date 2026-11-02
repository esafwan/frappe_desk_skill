"""Dashboard Chart and Number Card creation.

Required-field rules from frappe/desk/doctype/{dashboard_chart,number_card}/*.json (mandatory_depends_on):
- Number Card: is_standard=1 needs module; Document Type -> document_type, function, aggregate_function_based_on unless Count;
  Report -> report_name, report_field, report_function; Custom -> method.
- Dashboard Chart: chart_name, filters_json (reqd); is_standard=1 needs module.
"""

from __future__ import annotations

import json
from datetime import datetime

from .common import App, FdeskError, Result, entity_path, load, plan_write


def _json_text(text, what, default="[]"):
	if text is None:
		return default
	try:
		json.loads(text)
	except ValueError as e:
		raise FdeskError(f"{what} is not valid JSON: {e}")
	return text


def _write_new(app, kind, name, module, spec, doctype) -> Result:
	"""Create the entity, no-op when it already matches, error when the same name exists with different settings."""
	path = entity_path(app, kind, name, module)
	res = Result()
	if path.exists():
		cur = load(path)
		for k, v in spec.items():
			if cur.get(k) != v:
				raise FdeskError(f"{doctype} {name!r} exists with {k}={cur.get(k)!r} (requested {v!r})")
		return res
	now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
	doc = {**spec, "doctype": doctype, "docstatus": 0, "owner": "Administrator", "modified_by": "Administrator", "creation": now}
	plan_write(res, path, doc)
	return res


def cmd_chart_new(args) -> Result:
	app = App.find(args.app)
	ct = args.chart_type
	spec = {"name": args.name, "chart_name": args.name, "chart_type": ct, "is_standard": 1, "is_public": 1, "module": args.module,
		"filters_json": _json_text(args.filters, "--filters"), "type": args.type or ("Bar" if ct in ("Group By", "Report") else "Line")}
	if ct == "Report":
		if not args.report:
			raise FdeskError("--report is required for chart-type Report")
		spec.update(report_name=args.report, use_report_chart=1, timeseries=0)
	else:
		if not args.document_type:
			raise FdeskError(f"--document-type is required for chart-type {ct}")
		spec["document_type"] = args.document_type
	if ct in ("Count", "Sum", "Average"):
		spec["timeseries"] = 1 if args.timeseries else 0
		if args.timeseries and not args.based_on:
			raise FdeskError("--timeseries needs --based-on <date field>")
		if args.based_on:
			spec["based_on"] = args.based_on
		if ct in ("Sum", "Average"):
			if not args.value_based_on:
				raise FdeskError(f"chart-type {ct} needs --value-based-on <numeric field>")
			spec["value_based_on"] = args.value_based_on
		elif args.value_based_on:
			raise FdeskError("--value-based-on only applies to Sum/Average")
		if args.timeseries:
			spec["time_interval"] = args.time_interval or "Monthly"
			spec["timespan"] = args.timespan or "Last Year"
		elif args.time_interval or args.timespan:
			raise FdeskError("--time-interval/--timespan need --timeseries")
	elif ct == "Group By":
		if not args.group_by:
			raise FdeskError("chart-type Group By needs --group-by <field>")
		spec["group_by_based_on"] = args.group_by
		spec["group_by_type"] = args.group_by_type or "Count"
		spec["number_of_groups"] = args.number_of_groups
		if spec["group_by_type"] != "Count":
			if not args.value_based_on:
				raise FdeskError("Group By with Sum/Average needs --value-based-on <numeric field>")
			spec["aggregate_function_based_on"] = args.value_based_on
	return _write_new(app, "dashboard_chart", args.name, args.module, spec, "Dashboard Chart")


def cmd_card_new(args) -> Result:
	app = App.find(args.app)
	spec = {"name": args.name, "label": args.name, "type": args.type, "is_standard": 1, "is_public": 1, "module": args.module,
		"filters_json": _json_text(args.filters, "--filters")}
	if args.dynamic_filters:
		spec["dynamic_filters_json"] = _json_text(args.dynamic_filters, "--dynamic-filters")
	if args.show_percentage_stats:
		spec["show_percentage_stats"] = 1
		spec["stats_time_interval"] = "Monthly"
	if args.type == "Document Type":
		if not args.document_type:
			raise FdeskError("--document-type is required for type Document Type")
		fn = args.function or "Count"
		spec.update(document_type=args.document_type, function=fn)
		if fn != "Count":
			if not args.aggregate_field:
				raise FdeskError(f"function {fn} needs --aggregate-field")
			spec["aggregate_function_based_on"] = args.aggregate_field
	elif args.type == "Report":
		if not (args.report and args.report_field and args.report_function):
			raise FdeskError("type Report needs --report, --report-field and --report-function")
		spec.update(report_name=args.report, report_field=args.report_field, report_function=args.report_function)
	else:
		if not args.method:
			raise FdeskError("type Custom needs --method (dotted path)")
		spec["method"] = args.method
		if args.document_type:
			spec["document_type"] = args.document_type
	return _write_new(app, "number_card", args.name, args.module, spec, "Number Card")


def cmd_list(args) -> Result:
	app = App.find(args.app)
	res = Result()
	for kind, key in (("dashboard_chart", "charts"), ("number_card", "number_cards")):
		items = []
		for mod in app.modules:
			d = entity_path(app, kind, "x", mod).parent.parent
			for f in sorted(d.glob("*/*.json")) if d.exists() else []:
				doc = load(f)
				items.append({"name": doc.get("name"), "module": doc.get("module") or mod, "type": doc.get("chart_type") or doc.get("type"),
					"document_type": doc.get("document_type"), "path": str(f)})
		res.info[key] = items
	return res


def register(sub):
	cg = sub.add_parser("chart", help="Dashboard Chart")
	cs = cg.add_subparsers(dest="cmd", required=True)
	p = cs.add_parser("new")
	p.add_argument("name")
	p.add_argument("--module", required=True)
	p.add_argument("--chart-type", required=True, choices=["Count", "Sum", "Average", "Group By", "Report"])
	p.add_argument("--document-type")
	p.add_argument("--based-on")
	p.add_argument("--group-by")
	p.add_argument("--group-by-type", choices=["Count", "Sum", "Average"])
	p.add_argument("--value-based-on")
	p.add_argument("--number-of-groups", type=int, default=0)
	p.add_argument("--report")
	p.add_argument("--timeseries", action="store_true")
	p.add_argument("--time-interval", choices=["Daily", "Weekly", "Monthly", "Quarterly", "Yearly"])
	p.add_argument("--timespan", choices=["Last Year", "Last Quarter", "Last Month", "Last Week", "Select Date Range"])
	p.add_argument("--type", choices=["Line", "Bar", "Pie", "Percentage", "Donut"])
	p.add_argument("--filters")
	p.set_defaults(func=cmd_chart_new)
	p = cs.add_parser("list")
	p.set_defaults(func=cmd_list)

	ng = sub.add_parser("number-card", help="Number Card")
	ns = ng.add_subparsers(dest="cmd", required=True)
	p = ns.add_parser("new")
	p.add_argument("name")
	p.add_argument("--module", required=True)
	p.add_argument("--type", required=True, choices=["Document Type", "Report", "Custom"])
	p.add_argument("--document-type")
	p.add_argument("--function", choices=["Count", "Sum", "Average", "Minimum", "Maximum"])
	p.add_argument("--aggregate-field")
	p.add_argument("--filters")
	p.add_argument("--dynamic-filters")
	p.add_argument("--show-percentage-stats", action=__import__("argparse").BooleanOptionalAction, default=False)
	p.add_argument("--report")
	p.add_argument("--report-field")
	p.add_argument("--report-function", choices=["Sum", "Average", "Minimum", "Maximum"])
	p.add_argument("--method")
	p.set_defaults(func=cmd_card_new)
	p = ns.add_parser("list")
	p.set_defaults(func=cmd_list)
