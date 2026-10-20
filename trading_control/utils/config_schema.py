"""Validation rules for EA config, mirroring the Hamza_TVBridge inputs.

Ranges are portal-side sanity bounds; the EA additionally clamps to its chart-input caps
(caps_json reported by the EA), and the receiver re-validates. sl_usd must always be > 0.
magic, demo_only and timer_ms are chart-only and can never be set remotely.
"""

import frappe
from frappe import _

CONFIG_FIELDS = [
	"lot_mode", "lots", "risk_pct", "sl_usd", "tp_usd", "partial_at_usd", "partial_pct",
	"be_at_usd", "be_offset_usd", "max_open", "opposite", "symbol_map", "session_start",
	"session_end", "daily_loss_pct", "max_dd_pct", "max_spread_pts", "ttl_sec", "deviation_pts",
	"trading_enabled",
]

INT_FIELDS = {"lot_mode", "max_open", "opposite", "max_spread_pts", "ttl_sec", "deviation_pts", "trading_enabled"}

# (min, max, min_exclusive)
RANGES = {
	"lot_mode": (0, 1, False),
	"lots": (0.01, 100, False),
	"risk_pct": (0, 10, False),
	"sl_usd": (0, 100000, True),
	"tp_usd": (0, 100000, False),
	"partial_at_usd": (0, 100000, False),
	"partial_pct": (0, 100, False),
	"be_at_usd": (0, 100000, False),
	"be_offset_usd": (-100000, 100000, False),
	"max_open": (1, 50, False),
	"opposite": (0, 2, False),
	"daily_loss_pct": (0, 100, False),
	"max_dd_pct": (0, 100, False),
	"max_spread_pts": (0, 100000, False),
	"ttl_sec": (0, 86400, False),
	"deviation_pts": (0, 10000, False),
	"trading_enabled": (0, 1, False),
}

# Fields whose value must be <= the chart cap reported by the EA (caps_json key = same name)
CAPPED = ["lots", "risk_pct", "max_open", "daily_loss_pct", "max_dd_pct", "max_spread_pts", "deviation_pts", "ttl_sec"]

FORBIDDEN_KEYS = {"magic", "demo_only", "timer_ms", "inpmagic", "inpdemoonly", "inptimerms", "allow_remote_config"}


def _norm(key: str) -> str:
	return str(key).lower().replace("-", "_")


def check_forbidden(data: dict, where: str = "config"):
	if not isinstance(data, dict):
		return
	for k, v in data.items():
		nk = _norm(k)
		if nk in FORBIDDEN_KEYS or nk.replace("_", "") in FORBIDDEN_KEYS:
			frappe.throw(_("{0}: '{1}' is chart-only and cannot be changed remotely.").format(where, k))
		if isinstance(v, dict):
			check_forbidden(v, where)


def _hhmm(v) -> bool:
	if not v:
		return True
	try:
		h, m = str(v).split(":")
		return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
	except ValueError:
		return False


def validate_values(values: dict, caps: dict | None = None, require_sl: bool = True) -> list[str]:
	"""Return a list of error strings (empty if valid)."""
	errors = []
	check_forbidden(values)
	unknown = set(values) - set(CONFIG_FIELDS) - {"params_json"}
	if unknown:
		errors.append(_("Unknown config keys: {0}").format(", ".join(sorted(unknown))))
	if require_sl and not (values.get("sl_usd") and float(values.get("sl_usd")) > 0):
		errors.append(_("SL (USD) must be greater than 0."))
	for key, (lo, hi, excl) in RANGES.items():
		if values.get(key) in (None, ""):
			continue
		try:
			val = float(values[key])
		except (TypeError, ValueError):
			errors.append(_("{0} must be a number").format(key))
			continue
		if (val <= lo if excl else val < lo) or val > hi:
			errors.append(_("{0}={1} out of range {2}{3}..{4}").format(key, val, "(" if excl else "[", lo, hi))
	for key in ("session_start", "session_end"):
		if not _hhmm(values.get(key)):
			errors.append(_("{0} must be HH:MM (server time)").format(key))
	for key in CAPPED:
		cap = (caps or {}).get(key)
		if cap not in (None, "") and values.get(key) not in (None, "") and float(values[key]) > float(cap):
			errors.append(_("{0}={1} exceeds chart cap {2}").format(key, values[key], cap))
	return errors


def config_dict(doc) -> dict:
	out = {}
	for f in CONFIG_FIELDS:
		v = doc.get(f)
		if f in INT_FIELDS and v not in (None, ""):
			v = int(v)
		out[f] = v
	return out


def get_caps(ea_instance: str) -> dict:
	caps = frappe.db.get_value("EA Instance", ea_instance, "caps_json")
	if isinstance(caps, str):
		caps = frappe.parse_json(caps or "{}")
	return caps or {}
