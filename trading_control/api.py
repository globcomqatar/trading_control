"""Whitelisted APIs.

VPS (TC Integration):   ingest_status, ingest_events, ingest_ack, desired_config
Hamza (Trading Assistant): hamza_reply, create_change_request, create_trade_signal, create_journal_review
Desk users:             send_hamza_message, get_conversation
TC Approver:            review_change_request, decide_trade_signal, retry_push

Auth: `Authorization: token <api_key>:<api_secret>` (keys are generated per user in the desk).
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from trading_control import audit

INTEGRATION = "TC Integration"
ASSISTANT = "Trading Assistant"
APPROVER = "TC Approver"
CHAT_ROLES = ("TC Viewer", "TC Admin", "TC Approver", "System Manager")


def _payload(value):
	if isinstance(value, str):
		value = frappe.parse_json(value)
	return value or {}


def _require_instance(name):
	if not name or not frappe.db.exists("EA Instance", name):
		frappe.throw(_("Unknown EA Instance {0}").format(name), frappe.DoesNotExistError)


# ---------------------------------------------------------------- VPS (Integration)

STATUS_FIELDS = ["account", "demo", "equity", "day_pnl_pct", "dd_pct", "halted", "day_locked", "open",
	"last_id", "version", "applied_config_version"]


@frappe.whitelist(methods=["POST"])
def ingest_status(instance: str, status=None):
	"""Upsert EA Status (last-write-wins by ea_time) and append to EA Status History."""
	frappe.only_for(INTEGRATION)
	_require_instance(instance)
	data = _payload(status)
	ea_time = get_datetime(data.get("ea_time")) if data.get("ea_time") else None
	now = now_datetime()
	existing = frappe.db.get_value("EA Status", instance, ["name", "ea_time"], as_dict=True)
	if existing and existing.ea_time and ea_time and ea_time <= existing.ea_time:
		return {"ok": True, "ignored": "older_or_duplicate"}
	values = {k: data.get(k) for k in STATUS_FIELDS if k in data}
	values.update({"ea_time": ea_time, "received_at": now})
	if existing:
		doc = frappe.get_doc("EA Status", instance)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({"doctype": "EA Status", "ea_instance": instance, **values}).insert(ignore_permissions=True)
	frappe.get_doc({"doctype": "EA Status History", "ea_instance": instance,
		**{k: values.get(k) for k in ("equity", "day_pnl_pct", "dd_pct", "open", "halted", "day_locked", "ea_time")},
		"received_at": now}).insert(ignore_permissions=True)
	inst_upd = {"last_seen": now}
	for src, dst in (("ea_version", "ea_version"), ("version", "ea_version"), ("demo", "is_demo"), ("caps", "caps_json")):
		if data.get(src) is not None:
			inst_upd[dst] = frappe.as_json(data[src]) if dst == "caps_json" else data[src]
	if frappe.db.get_value("EA Instance", instance, "sync_state") == "Stale":
		inst_upd["sync_state"] = "In Sync"
	frappe.db.set_value("EA Instance", instance, inst_upd)
	frappe.publish_realtime("ea_status", {"ea_instance": instance, **{k: str(v) for k, v in values.items()}}, after_commit=True)
	return {"ok": True}


TRADE_FIELDS = ["ticket", "deal_id", "symbol", "side", "volume", "price_open", "sl", "tp", "price_close",
	"profit", "event", "cmd_id", "reason"]
SIGNAL_FIELDS = ["source", "symbol", "action", "result", "reason"]


@frappe.whitelist(methods=["POST"])
def ingest_events(events=None):
	"""Batch (<=100) of {type: trade|signal, event_id, instance, ...}. Idempotent on event_id/cmd_id."""
	frappe.only_for(INTEGRATION)
	events = _payload(events)
	if not isinstance(events, list) or len(events) > 100:
		frappe.throw(_("events must be a list of at most 100 items"))
	created, duplicate, errors = 0, 0, []
	for i, ev in enumerate(events):
		try:
			kind = ev.get("type")
			_require_instance(ev.get("instance"))
			if kind == "trade":
				event_id = ev.get("event_id") or f"{ev.get('deal_id') or ev.get('cmd_id')}:{ev.get('event')}"
				if frappe.db.exists("EA Trade Log", {"event_id": event_id}):
					duplicate += 1
					continue
				frappe.get_doc({"doctype": "EA Trade Log", "event_id": event_id, "ea_instance": ev["instance"],
					"event_time": get_datetime(ev.get("event_time")) if ev.get("event_time") else now_datetime(),
					**{k: ev.get(k) for k in TRADE_FIELDS if k in ev}}).insert(ignore_permissions=True)
			elif kind == "signal":
				cmd_id = ev.get("cmd_id") or ev.get("event_id")
				if not cmd_id:
					raise frappe.ValidationError("cmd_id required")
				if frappe.db.exists("EA Signal Log", cmd_id):
					duplicate += 1
					continue
				frappe.get_doc({"doctype": "EA Signal Log", "cmd_id": cmd_id, "ea_instance": ev["instance"],
					"received_at": get_datetime(ev.get("received_at")) if ev.get("received_at") else now_datetime(),
					**{k: ev.get(k) for k in SIGNAL_FIELDS if k in ev}}).insert(ignore_permissions=True)
			else:
				raise frappe.ValidationError(f"unknown type {kind}")
			created += 1
		except frappe.DuplicateEntryError:
			duplicate += 1
		except Exception as e:
			errors.append({"index": i, "error": str(e)[:200]})
	return {"ok": not errors, "created": created, "duplicate": duplicate, "errors": errors}


@frappe.whitelist(methods=["POST"])
def ingest_ack(instance: str, version: int, result: str, config_hash: str | None = None, errors=None, ack_time=None):
	frappe.only_for(INTEGRATION)
	_require_instance(instance)
	name = frappe.db.get_value("EA Config Version", {"ea_instance": instance, "version": int(version), "docstatus": 1})
	if not name:
		frappe.throw(_("No submitted config version {0} for {1}").format(version, instance))
	doc = frappe.get_doc("EA Config Version", name)
	if doc.ack_at and doc.ack_result == result:
		return {"ok": True, "ignored": "duplicate"}
	if config_hash and config_hash != doc.config_hash:
		result, errors = "rejected", (_payload(errors) or []) + ["config_hash mismatch"]
	applied = result == "applied"
	doc.db_set({"ack_at": get_datetime(ack_time) if ack_time else now_datetime(),
		"ack_result": "applied" if applied else "rejected",
		"ack_message": frappe.as_json(_payload(errors) or [])[:1000],
		"status": "Applied" if applied else "Rejected"})
	inst_upd = {"sync_state": "In Sync" if applied else "Rejected"}
	if applied:
		inst_upd["active_config"] = name
		for old in frappe.get_all("EA Config Version", filters={"ea_instance": instance, "docstatus": 1,
			"status": "Applied", "name": ["!=", name]}, pluck="name"):
			frappe.db.set_value("EA Config Version", old, "status", "Superseded")
	frappe.db.set_value("EA Instance", instance, inst_upd)
	audit.log("ack", instance, doc.doctype, name, after_hash=doc.config_hash, diff={"result": result})
	return {"ok": True}


@frappe.whitelist(methods=["GET"])
def desired_config(instance: str):
	"""Pull fallback: the latest approved/pushed version as the same HMAC-signed envelope as push."""
	frappe.only_for(INTEGRATION)
	_require_instance(instance)
	name = frappe.db.get_value("EA Instance", instance, "desired_config")
	if not name:
		return {"version": None}
	from trading_control.push import signed_envelope

	signed = signed_envelope(frappe.get_doc("EA Config Version", name))
	if not signed:
		return {"version": None, "error": "signing secret not configured"}
	body, headers = signed
	return {"body": body.decode(), "headers": {k: v for k, v in headers.items() if k.startswith("X-TC")}}


# ---------------------------------------------------------------- Hamza (Trading Assistant)


@frappe.whitelist(methods=["POST"])
def hamza_reply(conversation_user: str, body: str, in_reply_to: str | None = None,
	reference_doctype: str | None = None, reference_name: str | None = None):
	frappe.only_for(ASSISTANT)
	if in_reply_to and not (reference_doctype or reference_name):
		reference_doctype, reference_name = frappe.db.get_value(
			"Hamza Message", in_reply_to, ["reference_doctype", "reference_name"]) or (None, None)
	doc = frappe.get_doc({"doctype": "Hamza Message", "conversation_user": conversation_user,
		"direction": "From Hamza", "body": body, "in_reply_to": in_reply_to,
		"reference_doctype": reference_doctype, "reference_name": reference_name}).insert()
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def create_change_request(ea_instance: str, proposed_values, reasoning: str, based_on_version: str | None = None,
	evidence: str | None = None, source_message: str | None = None):
	frappe.only_for(ASSISTANT)
	doc = frappe.get_doc({"doctype": "Config Change Request", "ea_instance": ea_instance,
		"based_on_version": based_on_version, "proposed_values": frappe.as_json(_payload(proposed_values)),
		"reasoning": reasoning, "evidence": evidence, "source_message": source_message or None}).insert()
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def create_trade_signal(ea_instance: str, symbol: str, action: str, sl_usd: float, tp_usd: float | None = None,
	reason: str | None = None, expires_at: str | None = None, source_message: str | None = None):
	frappe.only_for(ASSISTANT)
	doc = frappe.get_doc({"doctype": "Trade Signal", "ea_instance": ea_instance, "symbol": symbol,
		"action": action, "sl_usd": sl_usd, "tp_usd": tp_usd, "reason": reason, "source": "hamza",
		"expires_at": expires_at, "source_message": source_message or None}).insert()
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def create_journal_review(summary: str, type: str = "Daily", ea_instance: str | None = None,
	period_from=None, period_to=None, lessons: str | None = None):
	frappe.only_for(ASSISTANT)
	doc = frappe.get_doc({"doctype": "Journal Review", "summary": summary, "type": type,
		"ea_instance": ea_instance, "period_from": period_from, "period_to": period_to,
		"lessons": lessons}).insert()
	return {"name": doc.name}


# ---------------------------------------------------------------- Desk chat


@frappe.whitelist(methods=["POST"])
def send_hamza_message(body: str, reference_doctype: str | None = None, reference_name: str | None = None):
	frappe.only_for(CHAT_ROLES)
	if reference_doctype and reference_name:
		frappe.get_doc(reference_doctype, reference_name).check_permission("read")
	doc = frappe.get_doc({"doctype": "Hamza Message", "conversation_user": frappe.session.user,
		"direction": "To Hamza", "body": body, "reference_doctype": reference_doctype or None,
		"reference_name": reference_name or None}).insert()
	return doc.as_dict()


@frappe.whitelist()
def get_conversation(limit: int = 100, before: str | None = None):
	filters = {"conversation_user": frappe.session.user}
	if before:
		filters["creation"] = ["<", before]
	rows = frappe.get_list("Hamza Message", filters=filters,
		fields=["name", "sender", "direction", "body", "status", "reference_doctype", "reference_name", "creation"],
		order_by="creation desc", limit_page_length=min(int(limit), 500))
	return list(reversed(rows))


# ---------------------------------------------------------------- Approver actions


@frappe.whitelist(methods=["POST"])
def review_change_request(name: str, decision: str, note: str | None = None):
	frappe.only_for(APPROVER)
	doc = frappe.get_doc("Config Change Request", name)
	doc.review(decision, note)
	return {"status": doc.status, "resulting_version": doc.resulting_version}


@frappe.whitelist(methods=["POST"])
def decide_trade_signal(name: str, decision: str):
	frappe.only_for(APPROVER)
	doc = frappe.get_doc("Trade Signal", name)
	doc.decide(decision)
	return {"status": doc.status}


@frappe.whitelist(methods=["POST"])
def retry_push(name: str):
	frappe.only_for(APPROVER)
	doc = frappe.get_doc("EA Config Version", name)
	if doc.docstatus != 1 or doc.status not in ("Approved", "Push Failed"):
		frappe.throw(_("Only Approved or Push Failed versions can be pushed"))
	doc.db_set({"status": "Approved", "push_attempts": 0, "next_push_at": None})
	from trading_control.push import enqueue_push

	enqueue_push(name)
	return {"ok": True}
