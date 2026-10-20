# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe.model.document import Document


class EAAuditLog(Document):
	def on_update(self):
		if not self.is_new() and self.get_doc_before_save():
			frappe.throw("EA Audit Log is insert-only.")

	def on_trash(self):
		if frappe.session.user != "Administrator":
			frappe.throw("EA Audit Log is insert-only.")
