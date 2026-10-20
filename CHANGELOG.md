# Changelog

## 0.2.0 — 2026-10-20
Raven integration (plan `RAVEN_PLAN_v2.md`). Raven stays an unmodified, **optional** app.

Added
- `trading_control/raven_router.py`: `receive()` (guest POST, verifies `X-Frappe-Webhook-Signature`,
  kill switch, dedupe, enqueues), `process_message` job (wake rules), `post_wake` job (HMAC-signed POST
  to the bot routine, `X-TC-Timestamp` + `X-TC-Signature`), `release_queues` (scheduler, every minute),
  `notify_decision()` doc hook (decision note back to the Raven thread).
- `trading_control/utils/raven_rules.py`: Frappe-free signature + wake-rule logic, with pytest tests
  (`trading_control/tests/test_raven_rules.py`).
- DocTypes: **Desk Bot**, **Desk Bot Settings** (Single, `enabled` default off), **Desk Wake Log**.
- `source_message` (Data, soft-checked against Raven Message when Raven is installed) on
  Config Change Request and Trade Signal; accepted by `api.create_change_request` / `create_trade_signal`.
- Workspace "Chat" shortcut and "Desk Bots (Raven)" card; navbar item "Chat (Raven)" → `/raven`.
- `docs/RAVEN_SETUP.md`; patch `v0_2.desk_bot_defaults`.

Changed (hardening)
- Config Change Request: new requests are always Draft with review fields cleared; status/review fields
  can only change through `review()` (TC Approver). Trade Signal: new signals always Pending with
  decision fields cleared; any decision-field change requires TC Approver. `source_message` immutable.

Needs staging verification (plan §8)
- Actual Raven Webhook payload (Webhook Data keys) arrives as JSON in the raw body (Frappe sends
  `as_json(data)` with no Content-Type) and the signature matches our verifier.
- Mention ids in `Raven Mention.user` equal the Frappe user / Desk Bot `raven_user`.
- Messages sent by bots via public `send_message` are not `is_bot_message`; owner-based detection works.
- Bot user with only Trading Assistant + Raven User can call `send_message` and read its channels.
- Thread model: Raven thread channels (`is_thread`, named after the root message) vs. quote replies
  (`is_reply`/`linked_message`) map to the right `thread_root`; DM threads resolve the DM peer.
- `notify_decision` posting as the approving user via `raven.api.raven_message.send_message` works
  (channel membership) and Raven installs on our v16 bench.
- Grok Bot routine trigger can verify `X-TC-Signature` (else use a shared-secret header).
- Navbar `/raven` item and Back-to-Desk path; guest endpoint is not blocked by site rate limits.

## 0.1.0
Phase 1: doctypes, roles, workflow, ingest APIs, signed push, Ask Hamza chat.
