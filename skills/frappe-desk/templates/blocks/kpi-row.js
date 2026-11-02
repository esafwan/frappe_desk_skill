const load = () => {
	const items = Array.isArray(CFG.items) ? CFG.items : [];
	if (!items.length) { setState("empty"); return; }
	return Promise.allSettled(items.map((it) => metric(it.doctype, it.filters, it.field, it.aggregate))).then((results) => {
		const wrap = q('[data-role="tiles"]');
		wrap.textContent = "";
		items.forEach((it, i) => {
			const tile = el("div", "fd-tile");
			tile.appendChild(el("div", "fd-title", it.label || it.doctype));
			const ok = results[i].status === "fulfilled";
			tile.appendChild(el("div", "fd-tile-value", ok ? (it.prefix || "") + fmtNum(results[i].value) + (it.suffix || "") : "—"));
			if (it.route) {
				const a = el("a", "fd-tile-link");
				bindRoute(a, it.route);
				a.appendChild(tile);
				wrap.appendChild(a);
			} else {
				wrap.appendChild(tile);
			}
		});
		setState(results.every((r) => r.status === "rejected") ? "error" : "ready");
	});
};
run(load);
