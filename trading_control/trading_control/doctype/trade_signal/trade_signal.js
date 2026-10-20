frappe.ui.form.on("Trade Signal", {
	refresh(frm) {
		trading_control.add_ask_hamza_button(frm);
		if (!frm.is_new() && frm.doc.status === "Pending" && frappe.user.has_role("TC Approver")) {
			const decide = (decision) => frappe.call("trading_control.api.decide_trade_signal",
				{ name: frm.doc.name, decision }).then(() => frm.reload_doc());
			frm.add_custom_button(__("Approve"), () => decide("Approved"), __("Decide"));
			frm.add_custom_button(__("Reject"), () => decide("Rejected"), __("Decide"));
		}
	},
});
