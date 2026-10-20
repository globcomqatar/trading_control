# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class HamzaMessage(Document):
	def validate(self):
		if self.is_new():
			self.sender = frappe.session.user
			if "Trading Assistant" in frappe.get_roles() and self.direction == "From Hamza":
				self.status = "Received"
			else:
				# Humans can only write into their own conversation, towards Hamza
				self.direction = "To Hamza"
				self.conversation_user = frappe.session.user
				self.status = "Queued"
		if bool(self.reference_doctype) != bool(self.reference_name):
			frappe.throw(_("Set both Reference DocType and Reference Name, or neither."))

	def after_insert(self):
		from trading_control import hamza

		hamza.publish(self)
		if self.direction == "To Hamza":
			hamza.enqueue_send(self.name)
