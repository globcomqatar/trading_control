"""Push approved EA Config Versions to the VPS receiver (/config), HMAC-signed.

Runs only in background jobs. URL and secret come from Bridge Settings (set in the UI);
if they are empty or push is disabled the push is skipped and logged, and the pull
fallback (api.desired_config) still delivers the config.
"""

import uuid
from datetime import timedelta

import frappe
import requests
from frappe.utils import now_datetime

from trading_control.utils.config_schema import config_dict
from trading_control.utils.signing import canonical_json, sha256_hex, sign_headers

BACKOFF_MIN = [1, 2, 5, 10, 10]  # minutes between attempts -> max 6 tries
MAX_TRIES = 6
LOGGER = "trading_control.push"


def logger():
	return frappe.logger(LOGGER, allow_site=True)


def build_envelope(doc) -> dict:
	inst = frappe.get_doc("EA Instance", doc.ea_instance)
	cfg = config_dict(doc)
	if not inst.enabled:
		cfg["trading_enabled"] = 0
	if doc.params_json:
		cfg["params"] = frappe.parse_json(doc.params_json)
	return {
		"instance": inst.name,
		"terminal_path": inst.terminal_path,
		"files_subdir": inst.files_subdir,
		"magic": inst.magic,
		"version": doc.version,
		"issued_at": now_datetime().isoformat(),
		"nonce": str(uuid.uuid4()),
		"config": cfg,
		"config_hash": sha256_hex(canonical_json(cfg)),
	}


def signed_envelope(doc) -> tuple[bytes, dict] | None:
	secret = frappe.get_single("Bridge Settings").get_password("config_hmac_secret", raise_exception=False)
	if not secret:
		return None
	body = canonical_json(build_envelope(doc))
	return body, sign_headers(secret, body)


def enqueue_push(name: str):
	frappe.enqueue(
		"trading_control.push.push_config",
		queue="short",
		name=name,
		enqueue_after_commit=True,
		job_id=f"tc_push_{name}",
		deduplicate=True,
	)


def push_config(name: str):
	doc = frappe.get_doc("EA Config Version", name)
	if doc.docstatus != 1 or doc.status not in ("Approved", "Push Failed"):
		return
	settings = frappe.get_single("Bridge Settings")
	if not settings.push_enabled or not settings.config_url:
		logger().info(f"push skipped for {name}: push disabled or config_url not set (pull fallback applies)")
		doc.db_set("push_error", "Skipped: push disabled or config URL not set", update_modified=False)
		return
	signed = signed_envelope(doc)
	if not signed:
		logger().warning(f"push skipped for {name}: config_hmac_secret not set")
		doc.db_set("push_error", "Skipped: HMAC secret not set", update_modified=False)
		return
	body, headers = signed
	attempts = (doc.push_attempts or 0) + 1
	try:
		r = requests.post(settings.config_url, data=body, headers=headers, timeout=settings.push_timeout_sec or 15)
		if r.status_code in (200, 201, 202):
			doc.db_set({"status": "Pushed", "pushed_at": now_datetime(), "push_attempts": attempts,
				"push_error": None, "next_push_at": None})
			frappe.db.set_value("EA Instance", doc.ea_instance, "sync_state", "Pending")
			from trading_control import audit
			audit.log("push", doc.ea_instance, doc.doctype, doc.name, after_hash=doc.config_hash)
			return
		err = f"HTTP {r.status_code}: {r.text[:300]}"
	except requests.RequestException as e:
		err = f"{type(e).__name__}: {str(e)[:300]}"
	logger().warning(f"push {name} attempt {attempts} failed: {err}")
	if attempts >= MAX_TRIES:
		doc.db_set({"status": "Push Failed", "push_attempts": attempts, "push_error": err, "next_push_at": None})
		frappe.publish_realtime("tc_alert", {"message": f"Config push failed: {name}"}, doctype=doc.doctype, docname=name)
	else:
		doc.db_set({"push_attempts": attempts, "push_error": err,
			"next_push_at": now_datetime() + timedelta(minutes=BACKOFF_MIN[attempts - 1])})


def retry_due_pushes():
	for name in frappe.get_all(
		"EA Config Version",
		filters={"docstatus": 1, "status": "Approved", "next_push_at": ["<=", now_datetime()]},
		pluck="name",
	):
		enqueue_push(name)
