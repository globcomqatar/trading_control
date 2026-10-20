# Copyright (c) 2026, Waheed Ali
# License: MIT

from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from trading_control import audit

DECISION_FIELDS = ("status", "decided_by", "decided_at", "cmd_id", "result")


class TradeSignal(Document):
	def validate(self):
		if not self.sl_usd or self.sl_usd <= 0:
			frappe.throw(_("SL (USD) must be greater than 0."))
		if self.is_new():
			# proposers (incl. the Trading Assistant bots) can only ever create a Pending signal
			self.status = "Pending"
			for f in DECISION_FIELDS[1:]:
				self.set(f, None)
			from trading_control.raven_router import validate_source_message

			validate_source_message(self)
			if not self.expires_at:
				self.expires_at = now_datetime() + timedelta(minutes=5)
			if "Trading Assistant" in frappe.get_roles() and "TC Approver" not in frappe.get_roles():
				self.source = "hamza"
		else:
			if self.has_value_changed("source_message"):
				frappe.throw(_("Source Message cannot be changed."))
			if (any(self.has_value_changed(f) for f in DECISION_FIELDS) and not self.flags.system
				and "TC Approver" not in frappe.get_roles()):
				frappe.throw(_("Only TC Approver can decide trade signals."), frappe.PermissionError)

	def after_insert(self):
		audit.log("trade_signal", self.ea_instance, self.doctype, self.name)

	def decide(self, decision: str):
		if "TC Approver" not in frappe.get_roles():
			frappe.throw(_("Only TC Approver can decide trade signals."), frappe.PermissionError)
		if self.status != "Pending":
			frappe.throw(_("Signal is not pending."))
		if decision not in ("Approved", "Rejected"):
			frappe.throw(_("Invalid decision"))
		if decision == "Approved" and get_datetime(self.expires_at) < now_datetime():
			frappe.throw(_("Signal expired; expired signals are never sent."))
		self.status = decision
		self.decided_by = frappe.session.user
		self.decided_at = now_datetime()
		# Phase 1: no /signal endpoint yet; approved signals are recorded only, nothing is sent.
		self.save()
		audit.log("approve" if decision == "Approved" else "reject", self.ea_instance, self.doctype, self.name)
