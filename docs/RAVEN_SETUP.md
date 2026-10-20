# Raven + Desk Bots setup (trading_control v0.2.0)

Plan: `RAVEN_PLAN_v2.md`. Raven is installed **unmodified** as a separate app; all routing code
is in `trading_control/raven_router.py` (pure rules in `trading_control/utils/raven_rules.py`).
Every step below needs Waheed's OK and should be done on **staging first**.
No secret ever goes into chat, git, code or docs — only into Password fields / Grok Bot env secrets.

## 0. Raven site settings (once)
- Install Raven on the v16 private bench (unmodified). Raven Settings → `enable_ai_integration` = **off**.
- Create channel `#gold-desk`; note its Raven Channel id (name) for Desk Bot Settings.

## 1. Raven Webhook (once)
Raven → Settings → Webhooks → New (DocType **Raven Webhook**):
| field | value |
|---|---|
| `webhook_trigger` | **Message Sent** (only this trigger; edits/deletes are not needed) |
| `request_url` | `https://<site>/api/method/trading_control.raven_router.receive` |
| `enable_security` | on |
| `webhook_secret` | generate a long random value; paste the **same** value into Desk Bot Settings → Raven Webhook Secret |
| `timeout` | 5 |
| `webhook_data` (key = fieldname) | `name`, `owner`, `channel_id`, `text`, `is_reply`, `linked_message`, `is_edited`, `is_bot_message`, `message_type`, `mentions` |
| condition | none (or Channel Type, if you want to restrict) |

Notes (from source): Raven creates a Frappe **Webhook** for this record. Frappe sends
`frappe.as_json(data)` as the body and `X-Frappe-Webhook-Signature = base64(HMAC_SHA256(secret, body))`.
The router verifies that over the raw body with a constant-time compare and returns 401 on mismatch.
If `mentions` is not in the payload, the router re-reads `Raven Mention` rows from the DB.

## 2. Desk Bot Settings (Single)
- `enabled` = **off** until the staging tests pass (off = all bots silent; the kill switch).
- `raven_webhook_secret` = value from §1.
- `owner_user` = Waheed's user. `gold_desk_channel` = Raven Channel id of #gold-desk.
- `moderator` = Desk Bot "Hamza" (role_on_desk = moderator). `hop_limit` 2, `thread_reply_cap` 6.

## 3. Per-bot checklist (Hamza, Daniel, Rashid, Sara)
1. Frappe **User** e.g. `daniel@aiopctrading.com`: API-only, no password login; roles **Trading Assistant**
   and **Raven User** only. Never TC Approver / TC Admin / System Manager (Desk Bot refuses such users).
2. Generate API key/secret → store as Grok Bot env secrets (`TC_DANIEL_KEY`, `TC_DANIEL_SECRET`). Never in chat.
3. Raven: make sure a **Raven User** exists for that user (created when the Raven User role is added).
   Optionally a **Raven Bot** record with `is_ai_bot` = off and `allow_bot_to_write_documents` = off.
4. Add the bot to `#gold-desk` (DMs are created on first message).
5. Grok Bot **routine** with an inbound webhook trigger. It must:
   - verify `X-TC-Signature == hex(HMAC_SHA256(routine_secret, X-TC-Timestamp + "." + raw_body))`
     and reject if |now − X-TC-Timestamp| > 300 s;
   - body: `{"channel_id","hop","message_id","reason","thread_root"}` (reason: dm | mention |
     default_moderator | summary);
   - read the thread with its own API key, reply with
     `POST /api/method/raven.api.raven_message.send_message` (`channel_id`, `text`, `is_reply=1`,
     `linked_message=<thread_root or message_id>`), and only create **Draft** Config Change Requests /
     **Pending** Trade Signals (pass `source_message=<message_id>` so the decision note posts back).
6. trading_control → **Desk Bot**: display_name, bot_user, raven_user (defaults to bot_user),
   role_on_desk, routine_webhook_url (https), routine_secret, max_replies_per_hour, enabled.

## 4. Rules enforced by the router
- DM to a bot → that bot. @mentions → the mentioned bots (one at a time per thread, others queued
  until the active bot replies or 3 min pass; queue entries expire after 30 min).
- Waheed in #gold-desk without a mention → moderator. A bot message wakes another bot only when it
  @mentions it and hop ≤ hop_limit. Bots are identified by message **owner** (Desk Bot.bot_user),
  not `is_bot_message`.
- Hop limit / thread cap (6 bot replies per human message) / per-bot hourly cap → moderator woken
  **once** with reason `summary`; its summary reply wakes nobody.
- Waheed writes `stop` → thread muted (`resume` un-mutes). Edits, deletes, duplicates ignored.
- Everything is recorded in **Desk Wake Log** (also used for idempotency).
- Approve/Reject of a CCR / Trade Signal with `source_message` posts
  "Approved/Rejected by <user>" as a reply in that Raven thread (posted as the approving user).

## 5. Staging checks (plan §8 / §9)
See the list in CHANGELOG 0.2.0 "Needs staging verification".
