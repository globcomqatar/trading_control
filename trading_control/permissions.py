import frappe

FULL_ACCESS_ROLES = {"TC Approver", "Trading Assistant", "System Manager"}
APP_ROLES = {"TC Viewer", "TC Admin", "TC Approver", "System Manager"}


def has_app_permission():
	return bool(APP_ROLES & set(frappe.get_roles()))


def _full(user):
	return user == "Administrator" or bool(FULL_ACCESS_ROLES & set(frappe.get_roles(user)))


def hamza_message_query(user=None):
	user = user or frappe.session.user
	if _full(user):
		return ""
	return f"`tabHamza Message`.`conversation_user` = {frappe.db.escape(user)}"


def hamza_message_has_permission(doc, ptype=None, user=None, debug=False):
	user = user or frappe.session.user
	if _full(user):
		return True
	return doc.conversation_user == user
