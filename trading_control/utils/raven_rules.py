"""Pure (Frappe-free) logic for the Raven router: signatures and wake rules.

Everything here is deterministic and side-effect free so it can be unit-tested with plain
pytest. `trading_control.raven_router` gathers state from the database, calls `decide()`,
and performs the resulting actions (Wake Log rows, background jobs).
Secrets are always passed in by the caller; none are stored here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
from dataclasses import dataclass, field

TURN_WINDOW_SEC = 180  # one active bot per thread; others wait until it replies or 3 min pass
STOP_WORDS = {"stop"}
RESUME_WORDS = {"resume"}

# --------------------------------------------------------------------------- signatures


def raven_signature(secret: str, body: bytes) -> str:
	"""Frappe Webhook signature: base64(HMAC_SHA256(secret, body)).

	Frappe signs `frappe.as_json(data)` and sends exactly those bytes as the body, so the
	raw request body is what we verify.
	"""
	return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def verify_raven_signature(secret: str | None, body: bytes, header: str | bytes | None) -> bool:
	if not secret or not header or body is None:
		return False
	if isinstance(header, bytes):
		header = header.decode("ascii", "ignore")
	header = header.strip()
	if header.startswith("b'") and header.endswith("'"):  # defensive: str(bytes) form
		header = header[2:-1]
	return hmac.compare_digest(raven_signature(secret, body).encode(), header.encode())


def routine_signature(secret: str, ts: str, body: bytes) -> str:
	"""Outbound wake signature: hex(HMAC_SHA256(routine_secret, ts + "." + body))."""
	return hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


def routine_headers(secret: str, body: bytes, ts: int) -> dict:
	ts_s = str(int(ts))
	return {
		"Content-Type": "application/json",
		"X-TC-Timestamp": ts_s,
		"X-TC-Signature": routine_signature(secret, ts_s, body),
	}


def verify_routine_signature(secret: str, body: bytes, ts: str, sig: str, now: float, max_skew: int = 300) -> bool:
	"""Reference verifier for the Grok Bot routine side."""
	try:
		if abs(now - int(ts)) > max_skew:
			return False
	except (TypeError, ValueError):
		return False
	return hmac.compare_digest(routine_signature(secret, str(ts), body), sig or "")


# --------------------------------------------------------------------------- text helpers

_TAG = re.compile(r"<[^>]+>")


def plain_text(text: str | None) -> str:
	return " ".join(html.unescape(_TAG.sub(" ", text or "")).split())


def command_word(text: str | None) -> str:
	return plain_text(text).strip().strip(".!").lower()


# --------------------------------------------------------------------------- wake rules


@dataclass
class BotInfo:
	user: str  # Frappe user (message owner)
	name: str  # Desk Bot record name
	enabled: bool = True
	max_per_hour: int = 0  # 0 = no hourly cap
	woken_last_hour: int = 0


@dataclass
class Message:
	message_id: str
	sender: str  # Frappe user id (Raven Message.owner)
	channel_id: str
	text: str = ""
	mentions: list[str] = field(default_factory=list)  # Frappe user ids (already mapped)
	is_dm: bool = False
	dm_peer: str | None = None  # the other DM participant (Frappe user id)
	event: str = "Message Sent"
	is_edited: bool = False


@dataclass
class Settings:
	enabled: bool
	owner_user: str | None
	moderator: str | None  # Frappe user of the moderator bot
	gold_desk_channel: str | None = None
	hop_limit: int = 2
	thread_reply_cap: int = 6


@dataclass
class ThreadState:
	already_seen: bool = False  # message id already in Desk Wake Log
	muted: bool = False
	sender_hop: int | None = None  # hop the sending bot was woken at in this thread (None = never woken)
	sender_wake_reason: str | None = None
	bot_replies_since_human: int = 0  # bot replies in this thread since the last human message, incl. this one
	summary_sent: bool = False  # moderator already woken with reason=summary since the last human message
	active_bot: str | None = None
	active_since: float | None = None
	active_replied: bool = True


@dataclass
class Wake:
	bot: str  # Frappe user of the bot
	hop: int
	reason: str  # dm | mention | default_moderator | summary
	queued: bool = False


@dataclass
class Decision:
	action: str  # ignore | wake | mute | unmute | none
	reason: str = ""
	wakes: list[Wake] = field(default_factory=list)
	skipped: list[tuple[str, str]] = field(default_factory=list)  # (bot, reason)
	sender_is_bot: bool = False


def thread_busy(state: ThreadState, now: float, window: int = TURN_WINDOW_SEC) -> bool:
	return bool(
		state.active_bot
		and not state.active_replied
		and state.active_since is not None
		and now - state.active_since < window
	)


def decide(msg: Message, bots: dict[str, BotInfo], settings: Settings, state: ThreadState, now: float) -> Decision:
	"""Return what the router should do with one incoming Raven message."""
	sender_is_bot = msg.sender in bots
	d = Decision(action="ignore", sender_is_bot=sender_is_bot)

	if not settings.enabled:
		d.reason = "kill_switch"
		return d
	if msg.event != "Message Sent" or msg.is_edited:
		d.reason = "not_new_message"
		return d
	if state.already_seen:
		d.reason = "duplicate"
		return d

	if not sender_is_bot and settings.owner_user and msg.sender == settings.owner_user:
		word = command_word(msg.text)
		if word in STOP_WORDS:
			return Decision(action="mute", reason="owner_stop")
		if word in RESUME_WORDS:
			return Decision(action="unmute", reason="owner_resume")
	if state.muted:
		d.reason = "thread_muted"
		return d

	mentioned = [u for u in dict.fromkeys(msg.mentions) if u in bots and u != msg.sender]
	targets: list[tuple[str, str]] = []
	summary_needed = False

	if sender_is_bot:
		if state.sender_hop is None:
			d.reason = "bot_not_woken"
			return d
		if state.sender_wake_reason == "summary":
			d.reason = "after_summary"
			return d
		hop = state.sender_hop + 1
		if state.bot_replies_since_human >= settings.thread_reply_cap:
			summary_needed = True
			d.skipped += [(u, "thread_reply_cap") for u in mentioned]
		elif hop > settings.hop_limit or state.sender_hop >= settings.hop_limit:
			# the chain cannot continue: hand back to the moderator for a summary
			summary_needed = True
			d.skipped += [(u, "hop_limit") for u in mentioned]
		else:
			targets = [(u, "mention") for u in mentioned]
	else:
		hop = 1
		if mentioned:
			targets = [(u, "mention") for u in mentioned]
		elif msg.is_dm and msg.dm_peer in bots:
			targets = [(msg.dm_peer, "dm")]
		elif (
			settings.gold_desk_channel
			and msg.channel_id == settings.gold_desk_channel
			and msg.sender == settings.owner_user
			and settings.moderator in bots
		):
			targets = [(settings.moderator, "default_moderator")]

	busy = thread_busy(state, now)
	for user, reason in targets:
		bot = bots[user]
		if not bot.enabled:
			d.skipped.append((user, "bot_disabled"))
			continue
		if bot.max_per_hour and bot.woken_last_hour >= bot.max_per_hour:
			d.skipped.append((user, "hourly_cap"))
			summary_needed = True
			continue
		queued = (busy and user != state.active_bot) or bool(d.wakes)
		d.wakes.append(Wake(bot=user, hop=hop, reason=reason, queued=queued))

	if summary_needed and not state.summary_sent and settings.moderator in bots:
		mod = bots[settings.moderator]
		if mod.enabled:
			d.wakes.append(Wake(bot=mod.user, hop=min(hop, settings.hop_limit), reason="summary", queued=bool(d.wakes)))

	if d.wakes:
		d.action = "wake"
		d.reason = ",".join(sorted({w.reason for w in d.wakes}))
	else:
		d.reason = d.reason or (d.skipped[0][1] if d.skipped else ("bot_no_mention" if sender_is_bot else "no_target"))
	return d


def thread_root(message_id: str, channel_is_thread: bool, channel_id: str, linked_root: str | None) -> str:
	"""Raven threads are channels named after the root message; quote-replies follow linked_message."""
	if channel_is_thread:
		return channel_id
	return linked_root or message_id
