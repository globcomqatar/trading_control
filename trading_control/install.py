import frappe


def after_install():
	# Defaults only; no secrets. Push stays disabled until Waheed enables it in Bridge Settings.
	bs = frappe.get_single("Bridge Settings")
	bs.max_clock_skew_sec = bs.max_clock_skew_sec or 60
	bs.stale_after_sec = bs.stale_after_sec or 120
	bs.push_timeout_sec = bs.push_timeout_sec or 15
	bs.save(ignore_permissions=True)
	hs = frappe.get_single("Hamza Settings")
	hs.timeout_sec = hs.timeout_sec or 10
	hs.save(ignore_permissions=True)
	set_desk_bot_defaults()


def set_desk_bot_defaults():
	# Bots stay silent (enabled=0) until Waheed turns the kill switch on. No secrets.
	ds = frappe.get_single("Desk Bot Settings")
	ds.hop_limit = ds.hop_limit or 2
	ds.thread_reply_cap = ds.thread_reply_cap or 6
	ds.save(ignore_permissions=True)
