# Copyright (c) 2026, Waheed Ali
# License: MIT

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import apply_workflow
from frappe.utils import now_datetime

from trading_control import audit
from trading_control.utils.config_schema import CONFIG_FIELDS, config_dict, get_caps, validate_values

REVIEW_FIELDS = ("status", "reviewed_by", "reviewed_at", "review_note", "resulting_version")


class ConfigChangeRequest(Document):
	def _values(self):
		v = frappe.parse_json(self.proposed_values) if isinstance(self.proposed_values, str) else self.proposed_values
		if not isinstance(v, dict):
			frappe.throw(_("Proposed Values must be a JSON object"))
		return v

	def _base(self):
		name = self.based_on_version or frappe.db.get_value("EA Instance", self.ea_instance, "active_config")
		return frappe.get_doc("EA Config Version", name) if name else None

	def validate(self):
		if self.is_new():
			# proposers (incl. the Trading Assistant bots) can only ever create a Draft
			self.status = "Draft"
			for f in REVIEW_FIELDS[1:]:
				self.set(f, None)
		elif not self.flags.reviewing and any(self.has_value_changed(f) for f in REVIEW_FIELDS):
			# review fields change only through review() (TC Approver), never via a plain write
			frappe.throw(_("Status and review fields can only be changed by a TC Approver review."),
				frappe.PermissionError)
		if not self.is_new() and self.has_value_changed("source_message"):
			frappe.throw(_("Source Message cannot be changed."))
		if self.is_new() or self.has_value_changed("source_message"):
			from trading_control.raven_router import validate_source_message

			validate_source_message(self)
		if not self.is_new() and self.get_doc_before_save() and self.get_doc_before_save().status != "Draft":
			if "TC Approver" not in frappe.get_roles():
				frappe.throw(_("Reviewed requests cannot be edited."))
		proposed = self._values()
		base = self._base()
		merged = {**(config_dict(base) if base else {}), **proposed}
		errors = validate_values(merged, get_caps(self.ea_instance))
		if errors:
			frappe.throw("<br>".join(errors), title=_("Invalid proposal"))
		old = config_dict(base) if base else {}
		self.diff = frappe.as_json({k: {"from": old.get(k), "to": v} for k, v in proposed.items() if old.get(k) != v})

	def after_insert(self):
		audit.log("change_request", self.ea_instance, self.doctype, self.name, diff=frappe.parse_json(self.diff))

	def review(self, decision: str, note: str | None = None):
		if "TC Approver" not in frappe.get_roles():
			frappe.throw(_("Only TC Approver can review change requests."), frappe.PermissionError)
		if self.status != "Draft":
			frappe.throw(_("Request already reviewed."))
		if decision not in ("Approved", "Rejected"):
			frappe.throw(_("Invalid decision"))
		if decision == "Approved":
			active = frappe.db.get_value("EA Instance", self.ea_instance, "active_config")
			if self.based_on_version and active and self.based_on_version != active:
				frappe.throw(_("Request is stale: based on {0} but active is {1}. Rebase first.").format(
					self.based_on_version, active))
			base = self._base()
			values = {**(config_dict(base) if base else {}), **self._values()}
			cv = frappe.get_doc({"doctype": "EA Config Version", "ea_instance": self.ea_instance,
				"change_note": f"From change request {self.name}: {(self.reasoning or '')[:200]}",
				"change_request": self.name,
				**{k: values.get(k) for k in CONFIG_FIELDS if k in values}})
			cv.insert()
			apply_workflow(cv, "Submit for Approval")
			cv.reload()
			apply_workflow(cv, "Approve")
			self.resulting_version = cv.name
		self.status = decision
		self.reviewed_by = frappe.session.user
		self.reviewed_at = now_datetime()
		self.review_note = note
		self.flags.reviewing = True
		self.save()
		audit.log("approve" if decision == "Approved" else "reject", self.ea_instance, self.doctype, self.name)
