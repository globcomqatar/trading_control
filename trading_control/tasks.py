from datetime import timedelta

import frappe
from frappe.utils import now_datetime


def expire_trade_signals():
	for name in frappe.get_all("Trade Signal",
		filters={"status": "Pending", "expires_at": ["<", now_datetime()]}, pluck="name"):
		frappe.db.set_value("Trade Signal", name, "status", "Expired")


def mark_stale_instances():
	stale = frappe.db.get_single_value("Bridge Settings", "stale_after_sec") or 120
	cutoff = now_datetime() - timedelta(seconds=int(stale))
	for row in frappe.get_all("EA Status", filters={"received_at": ["<", cutoff]}, fields=["ea_instance"]):
		if frappe.db.get_value("EA Instance", row.ea_instance, "sync_state") != "Stale":
			frappe.db.set_value("EA Instance", row.ea_instance, "sync_state", "Stale")


def prune_status_history(days: int = 90):
	frappe.db.delete("EA Status History", {"received_at": ["<", now_datetime() - timedelta(days=days)]})
