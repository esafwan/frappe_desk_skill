const items = Array.isArray(CFG.items) ? CFG.items : [];
const wrap = q('[data-role="rows"]');
items.forEach((it) => {
	const a = el("a", "fd-row");
	bindRoute(a, it.route);
	a.appendChild(el("span", "fd-row-title", it.label || it.route));
	if (it.description) a.appendChild(el("span", "fd-light", it.description));
	wrap.appendChild(a);
});
setState(items.length ? "ready" : "empty");
