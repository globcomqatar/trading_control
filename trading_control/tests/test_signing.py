import hashlib
import hmac
import unittest

from trading_control.utils.signing import canonical_json, sign_headers, verify


class TestSigning(unittest.TestCase):
	def test_roundtrip_and_vector(self):
		body = canonical_json({"b": 1, "a": "x"})
		self.assertEqual(body, b'{"a":"x","b":1}')
		h = sign_headers("test-secret", body, ts=1700000000, nonce="n1")
		msg = f"1700000000\nn1\n{hashlib.sha256(body).hexdigest()}".encode()
		self.assertEqual(h["X-TC-Signature"], hmac.new(b"test-secret", msg, hashlib.sha256).hexdigest())
		self.assertTrue(verify("test-secret", h, body, now=1700000010))
		self.assertFalse(verify("test-secret", h, body, now=1700000100))  # skew
		self.assertFalse(verify("other", h, body, now=1700000010))
		self.assertFalse(verify("test-secret", h, body + b" ", now=1700000010))
