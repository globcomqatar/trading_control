import frappe
from frappe.utils import now_datetime


def log(action, ea_instance=None, reference_doctype=None, reference_name=None, before_hash=None, after_hash=None, diff=None):
	"""Insert-only audit row. Never pass secrets in diff."""
	frappe.get_doc(
		{
			"doctype": "EA Audit Log",
			"ts": now_datetime(),
			"user": frappe.session.user,
			"ea_instance": ea_instance,
			"action": action,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"before_hash": before_hash,
			"after_hash": after_hash,
			"diff": frappe.as_json(diff) if diff is not None else None,
			"ip": getattr(frappe.local, "request_ip", None),
		}
	).insert(ignore_permissions=True)
