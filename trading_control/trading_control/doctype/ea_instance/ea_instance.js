frappe.ui.form.on("EA Instance", {
	refresh(frm) {
		trading_control.add_ask_hamza_button(frm);
		if (!frm.is_new()) {
			frm.add_custom_button(__("New Config Version"), () => {
				frappe.new_doc("EA Config Version", { ea_instance: frm.doc.name });
			});
		}
	},
});
