"""Raven <-> Grok Bot router (see RAVEN_PLAN_v2.md §3–§4, docs/RAVEN_SETUP.md).

receive()          Raven Webhook ("Message Sent", security on) -> verify, dedupe, enqueue.
process_message()  background job: gather state, apply wake rules, write Desk Wake Log.
post_wake()        background job: signed POST to the bot's Grok Bot routine webhook.
release_queues()   scheduler (every minute): dispatch queued wakes once a thread is free.
notify_decision()  doc_events hook: "Approved/Rejected by <user>" back into the Raven thread.

Raven is a soft dependency: nothing here imports `raven` at module level, and every DB
access to Raven DocTypes is guarded by `raven_installed()`.
"""

import json
import time
from datetime import timedelta

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from trading_control.utils import raven_rules as rules
from trading_control.utils.signing import canonical_json

SIGNATURE_HEADER = "X-Frappe-Webhook-Signature"
MAX_BODY = 256 * 1024
LOCK_TTL = 600
QUEUE_MAX_AGE_MIN = 30
WAKE_LOG = "Desk Wake Log"


def logger():
	return frappe.logger("trading_control.raven", allow_site=True)


def raven_installed() -> bool:
	return "raven" in frappe.get_installed_apps() and bool(frappe.db.exists("DocType", "Raven Message"))


def _settings():
	return frappe.get_cached_doc("Desk Bot Settings")


# --------------------------------------------------------------------------- inbound webhook


@frappe.whitelist(allow_guest=True, methods=["POST"])
def receive():
	"""Endpoint for the Raven Webhook. Returns quickly; all work happens in a job."""
	req = frappe.request
	body = req.get_data(cache=True) or b""
	if len(body) > MAX_BODY:
		frappe.throw("Payload too large", frappe.ValidationError)
	settings = _settings()
	secret = settings.get_password("raven_webhook_secret", raise_exception=False)
	if not rules.verify_raven_signature(secret, body, req.headers.get(SIGNATURE_HEADER)):
		frappe.local.response["http_status_code"] = 401
		logger().warning("raven webhook: bad or missing signature")
		return {"ok": False, "error": "invalid signature"}
	if not cint(settings.enabled):
		return {"ok": True, "ignored": "kill_switch"}
	try:
		payload = json.loads(body.decode("utf-8"))
	except Exception:
		frappe.local.response["http_status_code"] = 400
		return {"ok": False, "error": "invalid json"}
	if not isinstance(payload, dict):
		frappe.local.response["http_status_code"] = 400
		return {"ok": False, "error": "invalid payload"}
	message_id = str(payload.get("name") or payload.get("message_id") or "")
	if not message_id:
		return {"ok": True, "ignored": "no_message_id"}
	if frappe.db.exists(WAKE_LOG, {"message_id": message_id}):
		return {"ok": True, "ignored": "duplicate"}
	if not frappe.cache.set(frappe.cache.make_key(f"tc_raven_msg:{message_id}"), 1, ex=LOCK_TTL, nx=True):
		return {"ok": True, "ignored": "duplicate"}
	frappe.enqueue("trading_control.raven_router.process_message", queue="short", payload=payload,
		job_id=f"tc_raven_{message_id}", deduplicate=True, enqueue_after_commit=True)
	return {"ok": True, "queued": message_id}


# --------------------------------------------------------------------------- state gathering


def _bots():
	"""Map Frappe user -> BotInfo, and Raven user / bot user -> Frappe user."""
	rows = frappe.get_all("Desk Bot", fields=["name", "bot_user", "raven_user", "enabled", "max_replies_per_hour"])
	hour_ago = add_to_date(now_datetime(), hours=-1)
	bots, alias = {}, {}
	for r in rows:
		if not r.bot_user:
			continue
		count = frappe.db.count(WAKE_LOG, {"bot": r.name, "decision": "woken", "timestamp": [">=", hour_ago]})
		bots[r.bot_user] = rules.BotInfo(user=r.bot_user, name=r.name, enabled=bool(r.enabled),
			max_per_hour=cint(r.max_replies_per_hour), woken_last_hour=count)
		alias[r.bot_user] = r.bot_user
		if r.raven_user:
			alias[r.raven_user] = r.bot_user
	return bots, alias


def _mentions(payload, message_id):
	raw = payload.get("mentions")
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except Exception:
			raw = None
	users = []
	if isinstance(raw, list):
		users = [m.get("user") if isinstance(m, dict) else m for m in raw]
	if not users and raven_installed():  # payload may not carry the child table: re-read it
		users = frappe.get_all("Raven Mention", filters={"parent": message_id, "parenttype": "Raven Message"},
			pluck="user")
	return [u for u in users if u]


def _message(payload):
	"""Merge payload with the stored Raven Message (the DB is the source of truth when present)."""
	mid = str(payload.get("name") or payload.get("message_id"))
	data = dict(payload)
	if raven_installed() and frappe.db.exists("Raven Message", mid):
		row = frappe.db.get_value("Raven Message", mid,
			["owner", "channel_id", "text", "is_reply", "linked_message", "is_edited", "is_bot_message"], as_dict=True)
		data.update({k: v for k, v in row.items() if v is not None})
	return mid, data


def _channel(channel_id):
	if raven_installed() and channel_id and frappe.db.exists("Raven Channel", channel_id):
		return frappe.db.get_value("Raven Channel", channel_id,
			["name", "is_thread", "is_direct_message", "is_dm_thread", "dm_user_1", "dm_user_2", "channel_name"],
			as_dict=True)
	return frappe._dict(name=channel_id)


def _thread_root(mid, data, ch):
	linked_root = None
	if cint(data.get("is_reply")) and data.get("linked_message"):
		linked = data.get("linked_message")
		linked_root = frappe.db.get_value(WAKE_LOG, {"message_id": linked}, "thread_root") or linked
	return rules.thread_root(mid, bool(cint(ch.get("is_thread"))), ch.get("name"), linked_root)


def _dm_peer(ch, sender, alias):
	"""Other participant of a DM (or DM thread)."""
	if not cint(ch.get("is_direct_message")) and not cint(ch.get("is_dm_thread")):
		return None
	if cint(ch.get("is_dm_thread")) and raven_installed():
		parent = frappe.db.get_value("Raven Message", ch.name, "channel_id")
		if parent:
			ch = _channel(parent)
	peers = [alias.get(u, u) for u in (ch.get("dm_user_1"), ch.get("dm_user_2")) if u]
	others = [p for p in peers if p != sender]
	return others[0] if others else None


def _last(filters, fields=("name", "bot", "hop", "reason", "timestamp")):
	rows = frappe.get_all(WAKE_LOG, filters=filters, fields=list(fields), order_by="timestamp desc, creation desc",
		limit_page_length=1)
	return rows[0] if rows else None


def _thread_state(root, sender_bot):
	last_human = _last({"thread_root": root, "decision": "human_message"})
	since = last_human.timestamp if last_human else "2000-01-01"
	muted_row = _last({"thread_root": root, "decision": ["in", ["muted", "unmuted"]]}, fields=("name", "decision"))
	state = rules.ThreadState(muted=bool(muted_row and muted_row.decision == "muted"))
	state.summary_sent = bool(frappe.db.exists(WAKE_LOG, {"thread_root": root, "decision": ["in", ["woken", "queued"]],
		"reason": "summary", "timestamp": [">=", since]}))
	state.bot_replies_since_human = frappe.db.count(WAKE_LOG, {"thread_root": root, "decision": "bot_reply",
		"timestamp": [">=", since]})
	if sender_bot:
		state.bot_replies_since_human += 1  # this message
		w = _last({"thread_root": root, "bot": sender_bot.name, "decision": "woken"})
		if w:
			state.sender_hop, state.sender_wake_reason = cint(w.hop), w.reason
	active = _last({"thread_root": root, "decision": "woken"})
	if active:
		state.active_bot = frappe.db.get_value("Desk Bot", active.bot, "bot_user")
		state.active_since = get_datetime(active.timestamp).timestamp()
		replied = frappe.db.exists(WAKE_LOG, {"thread_root": root, "bot": active.bot, "decision": "bot_reply",
			"timestamp": [">=", active.timestamp]})
		state.active_replied = bool(replied) or bool(sender_bot and sender_bot.name == active.bot)
	return state


def _log(message_id, channel, root, decision, reason="", bot=None, hop=0, sender=None):
	return frappe.get_doc({"doctype": WAKE_LOG, "message_id": message_id, "channel": channel, "thread_root": root,
		"bot": bot, "hop": hop, "decision": decision, "reason": (reason or "")[:140], "sender": sender,
		"timestamp": now_datetime()}).insert(ignore_permissions=True)


# --------------------------------------------------------------------------- job: process


def process_message(payload: dict):
	settings = _settings()
	mid, data = _message(payload)
	if frappe.db.exists(WAKE_LOG, {"message_id": mid}):
		return
	bots, alias = _bots()
	sender = data.get("owner") or ""
	channel_id = data.get("channel_id")
	ch = _channel(channel_id)
	root = _thread_root(mid, data, ch)
	sender_bot = bots.get(sender)
	moderator_user = frappe.db.get_value("Desk Bot", settings.moderator, "bot_user") if settings.moderator else None
	msg = rules.Message(
		message_id=mid, sender=sender, channel_id=channel_id, text=data.get("text") or "",
		mentions=[alias.get(u, u) for u in _mentions(payload, mid)],
		is_dm=bool(cint(ch.get("is_direct_message")) or cint(ch.get("is_dm_thread"))),
		dm_peer=_dm_peer(ch, sender, alias),
		event=payload.get("event") or "Message Sent", is_edited=bool(cint(data.get("is_edited"))))
	gold = settings.gold_desk_channel
	if gold and ch.get("is_thread") and raven_installed():
		parent = frappe.db.get_value("Raven Message", ch.name, "channel_id")  # thread under #gold-desk
		if parent == gold:
			msg.channel_id = gold
	cfg = rules.Settings(enabled=bool(cint(settings.enabled)), owner_user=settings.owner_user, moderator=moderator_user,
		gold_desk_channel=gold, hop_limit=cint(settings.hop_limit) or 2,
		thread_reply_cap=cint(settings.thread_reply_cap) or 6)
	state = _thread_state(root, sender_bot)
	decision = rules.decide(msg, bots, cfg, state, time.time())

	# always record the message itself (idempotency + counters)
	seen_kind = "bot_reply" if sender_bot else "human_message"
	if decision.reason in ("kill_switch", "not_new_message", "duplicate"):
		_log(mid, channel_id, root, "ignored", decision.reason, sender=sender)
		return
	_log(mid, channel_id, root, seen_kind, decision.reason, bot=sender_bot.name if sender_bot else None,
		hop=state.sender_hop or 0, sender=sender)

	if decision.action in ("mute", "unmute"):
		_log(mid, channel_id, root, "muted" if decision.action == "mute" else "unmuted", decision.reason, sender=sender)
	for user, why in decision.skipped:
		_log(mid, channel_id, root, "ignored", why, bot=bots[user].name, sender=sender)
	for w in decision.wakes:
		row = _log(mid, channel_id, root, "queued" if w.queued else "woken", w.reason, bot=bots[w.bot].name,
			hop=w.hop, sender=sender)
		if not w.queued:
			enqueue_wake(row.name)
	frappe.db.commit()
	if sender_bot:
		release_thread(root)


# --------------------------------------------------------------------------- job: wake a bot


def enqueue_wake(log_name: str):
	frappe.enqueue("trading_control.raven_router.post_wake", queue="short", log_name=log_name,
		job_id=f"tc_wake_{log_name}", deduplicate=True, enqueue_after_commit=True)


def post_wake(log_name: str):
	import requests

	row = frappe.get_doc(WAKE_LOG, log_name)
	settings = _settings()
	if not cint(settings.enabled):
		row.db_set({"decision": "ignored", "reason": "kill_switch"})
		return
	bot = frappe.get_doc("Desk Bot", row.bot)
	secret = bot.get_password("routine_secret", raise_exception=False)
	if not bot.enabled or not bot.routine_webhook_url or not secret:
		row.db_set({"decision": "failed", "error": "bot disabled or routine URL/secret not set"})
		return
	body = canonical_json({"message_id": row.message_id, "channel_id": row.channel, "thread_root": row.thread_root,
		"hop": cint(row.hop), "reason": row.reason})
	headers = rules.routine_headers(secret, body, int(time.time()))
	try:
		r = requests.post(bot.routine_webhook_url, data=body, headers=headers, timeout=10)
		r.raise_for_status()
		row.db_set({"dispatched_at": now_datetime(), "error": None})
	except Exception as e:  # never log secrets/headers
		row.db_set({"decision": "failed", "error": f"{type(e).__name__}: {str(e)[:300]}"})
		logger().warning(f"wake {log_name} for {row.bot} failed: {type(e).__name__}")


# --------------------------------------------------------------------------- queue


def release_thread(root: str):
	"""Dispatch the oldest queued wake in a thread if no bot is currently active."""
	state = _thread_state(root, None)
	if rules.thread_busy(state, time.time()):
		return
	q = frappe.get_all(WAKE_LOG, filters={"thread_root": root, "decision": "queued"}, fields=["name", "timestamp"],
		order_by="timestamp asc, creation asc", limit_page_length=1)
	if not q:
		return
	if get_datetime(q[0].timestamp) < now_datetime() - timedelta(minutes=QUEUE_MAX_AGE_MIN):
		frappe.db.set_value(WAKE_LOG, q[0].name, {"decision": "ignored", "reason": "queue_expired"})
		return release_thread(root)
	frappe.db.set_value(WAKE_LOG, q[0].name, {"decision": "woken", "timestamp": now_datetime()})
	frappe.db.commit()
	enqueue_wake(q[0].name)


def release_queues():
	"""Scheduler, every minute."""
	if not frappe.db.exists("DocType", WAKE_LOG) or not cint(frappe.db.get_single_value("Desk Bot Settings", "enabled")):
		return
	roots = frappe.get_all(WAKE_LOG, filters={"decision": "queued"}, pluck="thread_root", distinct=True)
	for root in set(roots):
		try:
			release_thread(root)
		except Exception:
			frappe.log_error(title=f"release_thread {root}")


# --------------------------------------------------------------------------- decision note back to Raven


def notify_decision(doc, method=None):
	"""doc_events on_update for Config Change Request and Trade Signal."""
	if not doc.get("source_message") or doc.status not in ("Approved", "Rejected"):
		return
	if not doc.has_value_changed("status"):
		return
	if not raven_installed():
		return
	frappe.enqueue("trading_control.raven_router.post_decision_note", queue="short", enqueue_after_commit=True,
		doctype=doc.doctype, name=doc.name, user=frappe.session.user)


def post_decision_note(doctype: str, name: str, user: str):
	doc = frappe.get_doc(doctype, name)
	msg = doc.get("source_message")
	if not msg or not raven_installed() or not frappe.db.exists("Raven Message", msg):
		return
	channel_id = frappe.db.get_value("Raven Message", msg, "channel_id")
	who = frappe.utils.get_fullname(user) or user
	url = frappe.utils.get_url_to_form(doctype, name)
	text = f'<p>{doc.status} by {frappe.utils.escape_html(who)}: <a href="{url}">{doctype} {name}</a></p>'
	previous = frappe.session.user
	try:
		from raven.api.raven_message import send_message  # soft dependency

		frappe.set_user(user)
		send_message(channel_id=channel_id, text=text, is_reply=True, linked_message=msg)
		frappe.db.commit()
	except Exception:
		frappe.log_error(title=f"Raven decision note failed for {doctype} {name}")
	finally:
		frappe.set_user(previous)


def validate_source_message(doc, method=None):
	"""Soft check: if Raven is installed, source_message must be an existing Raven Message."""
	if doc.get("source_message") and raven_installed() and not frappe.db.exists("Raven Message", doc.source_message):
		frappe.throw(frappe._("Source Message {0} not found in Raven").format(doc.source_message))
