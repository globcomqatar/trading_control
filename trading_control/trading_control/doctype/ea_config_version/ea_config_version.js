frappe.ui.form.on("EA Config Version", {
	refresh(frm) {
		trading_control.add_ask_hamza_button(frm);
		frm.set_intro(__("Times on this form are Qatar time (Asia/Qatar, UTC+3) except session start/end, which are broker server time."));
		if (frm.doc.docstatus === 1 && ["Push Failed", "Approved"].includes(frm.doc.status)
			&& frappe.user.has_role("TC Approver")) {
			frm.add_custom_button(__("Retry Push"), () => {
				frappe.call("trading_control.api.retry_push", { name: frm.doc.name }).then(() => frm.reload_doc());
			});
		}
	},
});
