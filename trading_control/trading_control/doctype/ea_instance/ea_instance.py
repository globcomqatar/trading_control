# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class EAInstance(Document):
	def validate(self):
		if not self.magic or self.magic <= 0:
			frappe.throw(_("Magic must be a positive integer."))
		dup = frappe.db.get_value("EA Instance", {"terminal_path": self.terminal_path, "magic": self.magic,
			"name": ["!=", self.name]})
		if self.terminal_path and dup:
			frappe.throw(_("Magic {0} is already used on {1} by {2}").format(self.magic, self.terminal_path, dup))
