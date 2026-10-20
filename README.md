# Trading Control (`trading_control`)

Frappe app for Waheed's MT5 EAs: EA registry, approved config versions with an
HMAC-signed push to the VPS receiver, status/trade/signal feed from the VPS, and the
one-to-one **Ask Hamza** chat.

**Compatibility: Frappe v16 only** (`frappe>=16.0.0,<17.0.0`, Python `>=3.14,<3.15`, as
required by the Frappe `version-16` branch). Not tested on v15.

Phase 1 scope (design: `DESIGN.md` §2, §3, §4, §8a, §11). v0.2.0 adds the optional Raven
integration (Desk Bot router) — see `docs/RAVEN_SETUP.md` and `CHANGELOG.md`. Raven itself is a
separate, unmodified, optional app.

## What's inside
- DocTypes (module *Trading Control*): EA Instance, EA Config Version (submittable +
  workflow *EA Config Version Approval*: Draft → Pending Approval → Approved → Pushed →
  Applied / Rejected), EA Status, EA Status History, EA Trade Log, EA Signal Log,
  EA Audit Log, Bridge Settings (Single), Hamza Settings (Single), Config Change Request,
  Trade Signal, Journal Review (+ Journal Review Trade), Hamza Message.
- Roles (fixtures): TC Viewer, TC Admin, TC Approver, TC Integration, Trading Assistant.
- APIs: `trading_control/api.py` (see docstring).
- Push job: `trading_control/push.py`; Hamza webhook: `trading_control/hamza.py`.
  Signature = `hex(HMAC_SHA256(secret, ts + "\n" + nonce + "\n" + sha256_hex(body)))`,
  headers `X-TC-Timestamp`, `X-TC-Nonce`, `X-TC-Signature`.
- Desk page `/app/ask-hamza`, "Ask Hamza" buttons on EA Instance, EA Trade Log,
  EA Config Version (also Config Change Request, Trade Signal), Workspace *Trading Control*.

**No secrets are in this repo.** URLs and secrets are set after install in
*Bridge Settings* and *Hamza Settings* (Password fields). Push is disabled by default.

## Install (Frappe Cloud private bench, v16)
1. Push this repo to a private GitHub repo (e.g. `waheed/trading_control`).
2. Frappe Cloud → Bench Groups → your v16 bench → Apps → Add App → GitHub → pick the
   repo and branch (`develop` for staging, `main` for production). Grant the Frappe Cloud
   GitHub App access to this repo only.
3. Deploy the bench group (new build), then Sites → your site → Apps → Install App →
   Trading Control. Migrations, fixtures (roles, workflow, number cards) run automatically.
4. On the site:
   - System Settings: Time Zone `Asia/Qatar`; enable Two Factor Auth (TOTP).
   - Assign roles (TC Approver = Waheed only).
   - Create API-only users later (TC Integration for the VPS, Trading Assistant for
     Hamza) – separate approval per DESIGN §9.
   - Fill Bridge Settings / Hamza Settings when the VPS/Hamza side is ready.

## Local install (self-hosted bench)
```bash
bench get-app https://github.com/<owner>/trading_control --branch develop
bench --site <site> install-app trading_control
bench --site <site> migrate
```

## Tests
```bash
bench --site <test-site> run-tests --app trading_control
```

License: MIT
