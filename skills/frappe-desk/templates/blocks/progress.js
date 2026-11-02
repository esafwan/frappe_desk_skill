const load = () => Promise.all([count(CFG.doctype, CFG.filters), count(CFG.doctype, CFG.total_filters)]).then((res) => {
	const done = Number(res[0]) || 0;
	const total = Number(res[1]) || 0;
	if (!total) { setState("empty"); return; }
	const pct = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
	q('[data-role="percent"]').textContent = pct + "%";
	q('[data-role="detail"]').textContent = fmtNum(done) + " / " + fmtNum(total) + (CFG.label ? " " + CFG.label : "");
	q('[data-role="track"]').setAttribute("aria-valuenow", String(pct));
	q('[data-role="fill"]').style.width = pct + "%";
	setState("ready");
});
run(load);
