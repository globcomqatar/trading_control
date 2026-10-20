# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from trading_control import audit
from trading_control.utils.config_schema import check_forbidden, config_dict, get_caps, validate_values
from trading_control.utils.signing import canonical_json, sha256_hex

APPROVER = "TC Approver"
APPROVER_STATES = {"Approved", "Rejected"}


class EAConfigVersion(Document):
	def before_insert(self):
		last = frappe.db.sql(
			"select max(version) from `tabEA Config Version` where ea_instance=%s", self.ea_instance
		)[0][0]
		self.version = (last or 0) + 1

	def validate(self):
		values = config_dict(self)
		if self.params_json:
			check_forbidden(frappe.parse_json(self.params_json), "params_json")
		errors = validate_values(values, get_caps(self.ea_instance), require_sl=True)  # SL>0 always (owner rule)
		if errors:
			frappe.throw("<br>".join(errors), title=_("Invalid config"))
		self.config_hash = sha256_hex(canonical_json(values))
		self._check_approver_transition()

	def _check_approver_transition(self):
		before = self.get_doc_before_save()
		old = before.status if before else None
		if self.status in APPROVER_STATES and old != self.status and old == "Pending Approval":
			if APPROVER not in frappe.get_roles():
				frappe.throw(_("Only TC Approver can approve or reject a config version."), frappe.PermissionError)

	def before_submit(self):
		if APPROVER not in frappe.get_roles():
			frappe.throw(_("Only TC Approver can approve a config version."), frappe.PermissionError)
		self.status = "Approved"
		self.approved_by = frappe.session.user
		self.approved_at = now_datetime()

	def on_submit(self):
		frappe.db.set_value("EA Instance", self.ea_instance, {"desired_config": self.name, "sync_state": "Pending"})
		audit.log("approve", self.ea_instance, self.doctype, self.name, after_hash=self.config_hash)
		from trading_control.push import enqueue_push

		enqueue_push(self.name)

	def on_update(self):
		before = self.get_doc_before_save()
		if before and before.status != self.status and self.status == "Rejected":
			audit.log("reject", self.ea_instance, self.doctype, self.name, after_hash=self.config_hash)
		elif not before:
			audit.log("config_change", self.ea_instance, self.doctype, self.name, after_hash=self.config_hash)

	def on_cancel(self):
		self.db_set("status", "Superseded")
