"""Pure tests (no Frappe): run with `pytest trading_control/tests/test_raven_rules.py`."""

import base64
import hashlib
import hmac
import json

from trading_control.utils import raven_rules as r

OWNER = "waheed@example.com"
HAMZA, DANIEL, SARA = "hamza@example.com", "daniel@example.com", "sara@example.com"
GOLD = "gold-desk"
NOW = 1_800_000_000.0


def bots(**over):
	b = {u: r.BotInfo(user=u, name=u.split("@")[0].title(), max_per_hour=20) for u in (HAMZA, DANIEL, SARA)}
	for u, kw in over.items():
		for k, v in kw.items():
			setattr(b[u], k, v)
	return b


def settings(**kw):
	base = dict(enabled=True, owner_user=OWNER, moderator=HAMZA, gold_desk_channel=GOLD, hop_limit=2, thread_reply_cap=6)
	base.update(kw)
	return r.Settings(**base)


def msg(sender=OWNER, channel=GOLD, text="hi", mentions=(), **kw):
	return r.Message(message_id="m1", sender=sender, channel_id=channel, text=text, mentions=list(mentions), **kw)


def woken(d):
	return [(w.bot, w.hop, w.reason, w.queued) for w in d.wakes]


# ---------------------------------------------------------------- signatures


def test_raven_signature_matches_frappe_webhook():
	data = {"name": "m1", "owner": OWNER, "mentions": [{"user": DANIEL}]}
	body = json.dumps(data, indent=1, sort_keys=True).encode()  # frappe.as_json format
	expected = base64.b64encode(hmac.new(b"s3", body, hashlib.sha256).digest())
	assert r.verify_raven_signature("s3", body, expected)  # bytes header
	assert r.verify_raven_signature("s3", body, expected.decode())
	assert r.verify_raven_signature("s3", body, " " + expected.decode() + "\n")
	assert not r.verify_raven_signature("other", body, expected)
	assert not r.verify_raven_signature("s3", body + b" ", expected)
	assert not r.verify_raven_signature("s3", body, None)
	assert not r.verify_raven_signature(None, body, expected)
	assert not r.verify_raven_signature("", body, expected)
	assert not r.verify_raven_signature("s3", body, "garbage")


def test_routine_signature_roundtrip():
	body = b'{"hop":1,"message_id":"m1"}'
	h = r.routine_headers("rs", body, ts=1700000000)
	assert h["X-TC-Timestamp"] == "1700000000"
	assert h["X-TC-Signature"] == hmac.new(b"rs", b"1700000000." + body, hashlib.sha256).hexdigest()
	assert r.verify_routine_signature("rs", body, "1700000000", h["X-TC-Signature"], now=1700000010)
	assert not r.verify_routine_signature("rs", body, "1700000000", h["X-TC-Signature"], now=1700009999)
	assert not r.verify_routine_signature("xx", body, "1700000000", h["X-TC-Signature"], now=1700000010)
	assert not r.verify_routine_signature("rs", body, "nope", h["X-TC-Signature"], now=1700000010)


# ---------------------------------------------------------------- basic gates


def test_kill_switch_edit_duplicate():
	assert r.decide(msg(), bots(), settings(enabled=False), r.ThreadState(), NOW).reason == "kill_switch"
	assert r.decide(msg(is_edited=True), bots(), settings(), r.ThreadState(), NOW).reason == "not_new_message"
	assert r.decide(msg(event="Message Deleted"), bots(), settings(), r.ThreadState(), NOW).action == "ignore"
	assert r.decide(msg(), bots(), settings(), r.ThreadState(already_seen=True), NOW).reason == "duplicate"


# ---------------------------------------------------------------- who wakes


def test_dm_wakes_peer_bot():
	d = r.decide(msg(channel="dm1", is_dm=True, dm_peer=DANIEL), bots(), settings(), r.ThreadState(), NOW)
	assert woken(d) == [(DANIEL, 1, "dm", False)]


def test_owner_plain_gold_desk_wakes_moderator_only():
	d = r.decide(msg(), bots(), settings(), r.ThreadState(), NOW)
	assert woken(d) == [(HAMZA, 1, "default_moderator", False)]


def test_plain_message_elsewhere_or_from_others_wakes_nobody():
	assert r.decide(msg(channel="random"), bots(), settings(), r.ThreadState(), NOW).wakes == []
	assert r.decide(msg(sender="guest@example.com"), bots(), settings(), r.ThreadState(), NOW).wakes == []


def test_mentions_wake_in_turn():
	d = r.decide(msg(mentions=[SARA, DANIEL, SARA]), bots(), settings(), r.ThreadState(), NOW)
	assert woken(d) == [(SARA, 1, "mention", False), (DANIEL, 1, "mention", True)]


def test_disabled_bot_skipped():
	d = r.decide(msg(mentions=[DANIEL]), bots(**{DANIEL: {"enabled": False}}), settings(), r.ThreadState(), NOW)
	assert d.wakes == [] and d.skipped == [(DANIEL, "bot_disabled")]


# ---------------------------------------------------------------- bot to bot


def test_bot_without_mention_wakes_nobody():
	st = r.ThreadState(sender_hop=1, sender_wake_reason="dm", bot_replies_since_human=1)
	d = r.decide(msg(sender=DANIEL, text="answer"), bots(), settings(), st, NOW)
	assert d.wakes == [] and d.reason == "bot_no_mention" and d.sender_is_bot


def test_bot_mention_wakes_next_hop():
	st = r.ThreadState(sender_hop=1, sender_wake_reason="default_moderator", bot_replies_since_human=1)
	d = r.decide(msg(sender=HAMZA, mentions=[DANIEL, HAMZA]), bots(), settings(), st, NOW)
	assert woken(d) == [(DANIEL, 2, "mention", False)]


def test_hop_limit_triggers_single_summary():
	st = r.ThreadState(sender_hop=2, sender_wake_reason="mention", bot_replies_since_human=2)
	d = r.decide(msg(sender=DANIEL, mentions=[SARA]), bots(), settings(), st, NOW)
	assert woken(d) == [(HAMZA, 2, "summary", False)]
	assert (SARA, "hop_limit") in d.skipped
	st.summary_sent = True
	assert r.decide(msg(sender=DANIEL, mentions=[SARA]), bots(), settings(), st, NOW).wakes == []


def test_reply_after_summary_wakes_nobody():
	st = r.ThreadState(sender_hop=2, sender_wake_reason="summary", bot_replies_since_human=3, summary_sent=True)
	assert r.decide(msg(sender=HAMZA, mentions=[SARA]), bots(), settings(), st, NOW).reason == "after_summary"


def test_unwoken_bot_cannot_start_chain():
	assert r.decide(msg(sender=DANIEL, mentions=[SARA]), bots(), settings(), r.ThreadState(), NOW).reason == "bot_not_woken"


def test_thread_reply_cap():
	st = r.ThreadState(sender_hop=1, sender_wake_reason="mention", bot_replies_since_human=6)
	d = r.decide(msg(sender=DANIEL, mentions=[SARA]), bots(), settings(hop_limit=5), st, NOW)
	assert woken(d) == [(HAMZA, 2, "summary", False)] and (SARA, "thread_reply_cap") in d.skipped


def test_hourly_cap_triggers_summary():
	b = bots(**{DANIEL: {"woken_last_hour": 20}})
	d = r.decide(msg(mentions=[DANIEL]), b, settings(), r.ThreadState(), NOW)
	assert (DANIEL, "hourly_cap") in d.skipped and woken(d) == [(HAMZA, 1, "summary", False)]


# ---------------------------------------------------------------- turn taking, stop


def test_busy_thread_queues():
	st = r.ThreadState(active_bot=HAMZA, active_since=NOW - 60, active_replied=False)
	d = r.decide(msg(mentions=[DANIEL]), bots(), settings(), st, NOW)
	assert woken(d) == [(DANIEL, 1, "mention", True)]
	st.active_since = NOW - 200  # 3-minute window passed
	assert woken(r.decide(msg(mentions=[DANIEL]), bots(), settings(), st, NOW)) == [(DANIEL, 1, "mention", False)]
	assert r.thread_busy(r.ThreadState(active_bot=HAMZA, active_since=NOW, active_replied=True), NOW) is False


def test_stop_and_resume():
	assert r.decide(msg(text="<p>Stop!</p>"), bots(), settings(), r.ThreadState(), NOW).action == "mute"
	assert r.decide(msg(text="stop", sender=DANIEL), bots(), settings(), r.ThreadState(sender_hop=1), NOW).action != "mute"
	muted = r.ThreadState(muted=True)
	assert r.decide(msg(mentions=[DANIEL]), bots(), settings(), muted, NOW).reason == "thread_muted"
	assert r.decide(msg(text="resume"), bots(), settings(), muted, NOW).action == "unmute"


def test_worked_example_plan_section_4():
	b, s = bots(), settings()
	d0 = r.decide(msg(text="London open soon, gold long setup?"), b, s, r.ThreadState(), NOW)
	assert woken(d0) == [(HAMZA, 1, "default_moderator", False)]
	d1 = r.decide(msg(sender=HAMZA, mentions=[DANIEL]), b, s,
		r.ThreadState(sender_hop=1, sender_wake_reason="default_moderator", bot_replies_since_human=1), NOW)
	assert woken(d1) == [(DANIEL, 2, "mention", False)]
	d2 = r.decide(msg(sender=DANIEL, text="Bias long"), b, s,
		r.ThreadState(sender_hop=2, sender_wake_reason="mention", bot_replies_since_human=2), NOW)
	assert woken(d2) == [(HAMZA, 2, "summary", False)]
	d3 = r.decide(msg(mentions=["rashid@example.com", SARA]), b, s, r.ThreadState(), NOW)
	assert woken(d3) == [(SARA, 1, "mention", False)]  # unknown user is not a bot


def test_thread_root_and_plain_text():
	assert r.thread_root("m2", True, "root1", None) == "root1"
	assert r.thread_root("m2", False, "ch", "m1") == "m1"
	assert r.thread_root("m2", False, "ch", None) == "m2"
	assert r.plain_text("<p>a&amp;b <b>c</b></p>") == "a&b c"
