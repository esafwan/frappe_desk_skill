/* shared helpers; root_element is injected by Frappe (shadow root), query only through it */
const CFG = {{config}};
const block = root_element.querySelector(".fd-block");
const q = (sel) => block.querySelector(sel);
const T = typeof __ === "function" ? __ : (s) => s;
let currentLoad = null;
const setState = (s) => { block.dataset.state = s; };
const call = (method, args) => frappe.call({ method: method, args: args }).then((r) => (r ? r.message : null));
const count = (doctype, filters) => call("frappe.client.get_count", { doctype: doctype, filters: filters || {} });
const fmtNum = (v) => {
	const n = Number(v);
	return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v == null ? "" : v);
};
const go = (route) => {
	const parts = String(route || "").split("/").filter(Boolean);
	if (parts.length) frappe.set_route.apply(frappe, parts);
};
const bindRoute = (el, route) => {
	el.setAttribute("href", "#");
	el.addEventListener("click", (e) => { e.preventDefault(); go(route); });
};
const metric = (doctype, filters, field, agg) => {
	if (!field || !agg || agg === "count") return count(doctype, filters);
	const fn = agg === "avg" ? "avg" : "sum";
	return call("frappe.client.get_list", {
		doctype: doctype,
		filters: filters || {},
		fields: [fn + "(" + field + ") as value"],
		limit_page_length: 1,
	}).then((rows) => (rows && rows[0] && rows[0].value) || 0);
};
const el = (tag, cls, text) => {
	const node = block.ownerDocument.createElement(tag);
	if (cls) node.className = cls;
	if (text != null) node.textContent = text;
	return node;
};
const run = (load) => {
	currentLoad = load;
	setState("loading");
	Promise.resolve().then(load).catch((err) => { console.error(err); setState("error"); });
};
const retryBtn = q('[data-role="retry"]');
if (retryBtn) retryBtn.addEventListener("click", () => run(currentLoad));
const emptyMsg = q('[data-role="empty-text"]');
if (emptyMsg && CFG.empty_text) emptyMsg.textContent = CFG.empty_text;
