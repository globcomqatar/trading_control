"""Ask Hamza: one-to-one chat. Outbound signed webhook to Hamza; realtime to the user."""

from datetime import timedelta

import frappe
import requests
from frappe.utils import now_datetime

from trading_control.utils.signing import canonical_json, sign_headers

BACKOFF_MIN = [1, 2, 5, 10, 10]
MAX_TRIES = 6


def logger():
	return frappe.logger("trading_control.hamza", allow_site=True)


def publish(doc):
	data = {
		"name": doc.name,
		"conversation_user": doc.conversation_user,
		"sender": doc.sender,
		"direction": doc.direction,
		"body": doc.body,
		"status": doc.status,
		"reference_doctype": doc.reference_doctype,
		"reference_name": doc.reference_name,
		"creation": str(doc.creation),
	}
	frappe.publish_realtime("hamza_message", data, user=doc.conversation_user, after_commit=True)


def enqueue_send(name: str):
	frappe.enqueue("trading_control.hamza.send_to_hamza", queue="short", name=name,
		enqueue_after_commit=True, job_id=f"tc_hamza_{name}", deduplicate=True)


def send_to_hamza(name: str):
	doc = frappe.get_doc("Hamza Message", name)
	if doc.direction != "To Hamza" or doc.status not in ("Queued", "Failed"):
		return
	settings = frappe.get_single("Hamza Settings")
	secret = settings.get_password("webhook_secret", raise_exception=False) if settings.webhook_url else None
	if not settings.enabled or not settings.webhook_url or not secret:
		logger().info(f"hamza webhook skipped for {name}: disabled or webhook URL/secret not set")
		doc.db_set({"status": "Skipped", "last_error": "Hamza Settings disabled or not configured"})
		publish(doc)
		return
	payload = {
		"message_id": doc.name,
		"conversation_user": doc.conversation_user,
		"user": doc.sender,
		"text": doc.body,
		"doctype": doc.reference_doctype,
		"name": doc.reference_name,
		"ts": str(doc.creation),
		"site": frappe.local.site,
	}
	body = canonical_json(payload)
	attempts = (doc.attempts or 0) + 1
	try:
		r = requests.post(settings.webhook_url, data=body, headers=sign_headers(secret, body),
			timeout=settings.timeout_sec or 10)
		if 200 <= r.status_code < 300:
			doc.db_set({"status": "Sent", "attempts": attempts, "last_error": None, "next_attempt_at": None})
			publish(doc)
			return
		err = f"HTTP {r.status_code}: {r.text[:300]}"
	except requests.RequestException as e:
		err = f"{type(e).__name__}: {str(e)[:300]}"
	logger().warning(f"hamza webhook {name} attempt {attempts} failed: {err}")
	upd = {"status": "Failed", "attempts": attempts, "last_error": err, "next_attempt_at": None}
	if attempts < MAX_TRIES:
		upd["next_attempt_at"] = now_datetime() + timedelta(minutes=BACKOFF_MIN[attempts - 1])
	doc.db_set(upd)
	publish(doc)


def retry_due_messages():
	for name in frappe.get_all("Hamza Message",
		filters={"direction": "To Hamza", "status": "Failed", "next_attempt_at": ["<=", now_datetime()]},
		pluck="name"):
		enqueue_send(name)
