const load = () => {
	const titleField = CFG.title_field || "name";
	const subField = CFG.subtitle_field || "";
	const fields = ["name"];
	[titleField, subField].forEach((f) => { if (f && fields.indexOf(f) < 0) fields.push(f); });
	return call("frappe.client.get_list", {
		doctype: CFG.doctype,
		filters: CFG.filters || {},
		fields: fields,
		order_by: CFG.order_by || "modified desc",
		limit_page_length: Number(CFG.limit) || 5,
	}).then((rows) => {
		rows = rows || [];
		const wrap = q('[data-role="rows"]');
		wrap.textContent = "";
		rows.forEach((r) => {
			const a = el("a", "fd-row");
			bindRoute(a, "Form/" + CFG.doctype + "/" + r.name);
			a.appendChild(el("span", "fd-row-title", r[titleField] || r.name));
			if (subField && r[subField] != null) {
				const isDate = subField === "modified" || subField === "creation";
				const txt = isDate && frappe.datetime && frappe.datetime.comment_when ? frappe.datetime.comment_when(r[subField]) : String(r[subField]);
				a.appendChild(el("span", "fd-row-sub fd-light", txt));
			}
			wrap.appendChild(a);
		});
		setState(rows.length ? "ready" : "empty");
	});
};
run(load);
