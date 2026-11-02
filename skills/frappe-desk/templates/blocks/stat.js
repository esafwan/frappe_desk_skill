const load = () => metric(CFG.doctype, CFG.filters, CFG.field, CFG.aggregate).then((v) => {
	q('[data-role="value"]').textContent = (CFG.prefix || "") + fmtNum(v) + (CFG.suffix || "");
	const link = q('[data-role="link"]');
	if (CFG.route) { link.hidden = false; link.textContent = T("View all"); bindRoute(link, CFG.route); }
	setState(Number(v) === 0 && CFG.hide_zero ? "empty" : "ready");
});
run(load);
