// Shared desk helpers for trading_control (loaded via app_include_js).
frappe.provide("trading_control");

trading_control.open_ask_hamza = function (reference_doctype, reference_name) {
	frappe.route_options = reference_doctype ? { reference_doctype, reference_name } : null;
	frappe.set_route("ask-hamza");
};

trading_control.add_ask_hamza_button = function (frm) {
	if (frm.is_new()) return;
	if (!frappe.user.has_role(["TC Viewer", "TC Admin", "TC Approver", "System Manager"])) return;
	frm.add_custom_button(__("Ask Hamza"), () => trading_control.open_ask_hamza(frm.doctype, frm.doc.name));
};
