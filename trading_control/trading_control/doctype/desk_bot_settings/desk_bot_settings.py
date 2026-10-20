# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document


class DeskBotSettings(Document):
	def validate(self):
		if not 1 <= (self.hop_limit or 0) <= 5:
			frappe.throw(_("Hop Limit must be between 1 and 5"))
		if not 1 <= (self.thread_reply_cap or 0) <= 50:
			frappe.throw(_("Thread Reply Cap must be between 1 and 50"))
		if self.moderator and frappe.db.get_value("Desk Bot", self.moderator, "role_on_desk") != "moderator":
			frappe.throw(_("Moderator must be a Desk Bot with Role on Desk = moderator"))
