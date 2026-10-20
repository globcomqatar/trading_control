"""HMAC-SHA256 request signing shared by config push and the Hamza webhook.

signature = hex(HMAC_SHA256(secret, ts + "\\n" + nonce + "\\n" + sha256_hex(body)))
Headers: X-TC-Timestamp, X-TC-Nonce, X-TC-Signature (plus X-TC-Body-SHA256 for convenience).
Secrets are passed in by the caller (read from Password fields at call time), never stored here.
"""

import hashlib
import hmac
import json
import time
import uuid


def canonical_json(data) -> bytes:
	"""Sorted keys, no spaces, UTF-8. Used for both signing and config_hash."""
	return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()


def sha256_hex(body: bytes) -> str:
	return hashlib.sha256(body).hexdigest()


def compute_signature(secret: str, ts: str, nonce: str, body: bytes) -> str:
	msg = f"{ts}\n{nonce}\n{sha256_hex(body)}".encode()
	return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def sign_headers(secret: str, body: bytes, ts: int | None = None, nonce: str | None = None) -> dict:
	ts = str(int(ts if ts is not None else time.time()))
	nonce = nonce or str(uuid.uuid4())
	return {
		"Content-Type": "application/json",
		"X-TC-Timestamp": ts,
		"X-TC-Nonce": nonce,
		"X-TC-Body-SHA256": sha256_hex(body),
		"X-TC-Signature": compute_signature(secret, ts, nonce, body),
	}


def verify(secret: str, headers: dict, body: bytes, max_skew: int = 60, now: float | None = None) -> bool:
	"""Reference verifier (the receiver/shipper implement the same check)."""
	try:
		ts = headers["X-TC-Timestamp"]
		nonce = headers["X-TC-Nonce"]
		sig = headers["X-TC-Signature"]
	except KeyError:
		return False
	now = time.time() if now is None else now
	if abs(now - int(ts)) > max_skew:
		return False
	return hmac.compare_digest(compute_signature(secret, ts, nonce, body), sig)
