# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe.model.document import Document


class JournalReview(Document):
	def before_insert(self):
		self.author = frappe.session.user
