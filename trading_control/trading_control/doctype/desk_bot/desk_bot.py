# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document

FORBIDDEN_BOT_ROLES = {"TC Approver", "TC Admin", "System Manager", "Administrator"}


class DeskBot(Document):
	def validate(self):
		if self.routine_webhook_url and not self.routine_webhook_url.startswith("https://"):
			frappe.throw(_("Routine Webhook URL must use https://"))
		if self.max_replies_per_hour is not None and self.max_replies_per_hour < 0:
			frappe.throw(_("Max Replies per Hour cannot be negative"))
		if self.bot_user:
			bad = FORBIDDEN_BOT_ROLES & set(frappe.get_roles(self.bot_user))
			if bad:
				frappe.throw(_("Bot user {0} must not have roles: {1}").format(self.bot_user, ", ".join(sorted(bad))))
		if not self.raven_user:
			self.raven_user = self.bot_user
