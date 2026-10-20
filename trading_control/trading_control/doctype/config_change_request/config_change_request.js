frappe.ui.form.on("Config Change Request", {
	refresh(frm) {
		trading_control.add_ask_hamza_button(frm);
		if (!frm.is_new() && frm.doc.status === "Draft" && frappe.user.has_role("TC Approver")) {
			const review = (decision) => frappe.prompt(
				{ fieldname: "note", fieldtype: "Small Text", label: __("Review Note") },
				(v) => frappe.call("trading_control.api.review_change_request",
					{ name: frm.doc.name, decision, note: v.note }).then(() => frm.reload_doc()),
				decision === "Approved" ? __("Approve") : __("Reject"));
			frm.add_custom_button(__("Approve"), () => review("Approved"), __("Review"));
			frm.add_custom_button(__("Reject"), () => review("Rejected"), __("Review"));
		}
	},
});
