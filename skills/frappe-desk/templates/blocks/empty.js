q('[data-role="message"]').textContent = CFG.message || "";
if (CFG.action_label && CFG.action_route) {
	const a = q('[data-role="action"]');
	a.hidden = false;
	a.textContent = CFG.action_label;
	bindRoute(a, CFG.action_route);
}
setState("ready");
