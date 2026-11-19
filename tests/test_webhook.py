"""Offline tests for webhook.py - no scale and no real server needed.

Run:  python -m unittest discover -s tests -v
"""

import hashlib
import hmac
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import webhook  # noqa: E402

SECRET = "test-secret-not-real"


def laravel_signature(raw_body, secret):
    """Python port of ValidateIPS::hashing():
    hash_hmac('sha256', json_encode(json_decode($body, true)), $secret)"""
    reencoded = webhook.php_json_encode(json.loads(raw_body))
    return hmac.new(secret.encode(), reencoded, hashlib.sha256).hexdigest()


class PayloadTests(unittest.TestCase):
    def test_payload_shape(self):
        p = webhook.build_payload("EE123456785KH", "2.35", "kg")
        self.assertEqual(p, [{"MAILITM_FID": "EE123456785KH", "WEIGHT": 2.35,
                              "WEIGHT_UNIT": "kg", "SOURCE": "METTLER_TOLEDO_IND231"}])

    def test_weight_numbers(self):
        self.assertEqual(webhook.validate_weight("2.70", "kg"), 2.7)
        self.assertEqual(webhook.validate_weight("1990", "g"), 1990)
        self.assertIsInstance(webhook.validate_weight("1990", "g"), int)
        for bad in ("0", "0.00", "-1.2", "abc", "", None, "nan", "inf"):
            with self.assertRaises(ValueError, msg=bad):
                webhook.validate_weight(bad, "kg")
        with self.assertRaises(ValueError):
            webhook.validate_weight("1.0", "")

    def test_body_is_exact_php_format(self):
        body = webhook.php_json_encode(webhook.build_payload("EE123456785KH", "2.35", "kg"))
        self.assertEqual(body, b'[{"MAILITM_FID":"EE123456785KH","WEIGHT":2.35,'
                               b'"WEIGHT_UNIT":"kg","SOURCE":"METTLER_TOLEDO_IND231"}]')
        self.assertEqual(webhook.php_json_encode({"a": "x/y", "b": "é"}),
                         b'{"a":"x\\/y","b":"\\u00e9"}')

    def test_tracking(self):
        self.assertEqual(webhook.normalize_tracking(" ee 123-456-785 kh "), "EE123456785KH")
        self.assertTrue(webhook.S10_RE.match("EE123456785KH"))
        self.assertFalse(webhook.S10_RE.match("EE12345678KH"))
        self.assertTrue(webhook.s10_check_digit_ok("EE123456785KH"))
        self.assertFalse(webhook.s10_check_digit_ok("EE123456789KH"))


class SignatureTests(unittest.TestCase):
    def test_hmac_hex_lowercase(self):
        sig = webhook.sign(b"[]", "key")
        self.assertEqual(sig, hmac.new(b"key", b"[]", hashlib.sha256).hexdigest())
        self.assertRegex(sig, r"^[0-9a-f]{64}$")

    def test_matches_laravel_reencoding(self):
        for weight, unit in (("2.35", "kg"), ("1990", "g"), ("0.01", "kg"), ("123.456", "kg")):
            body = webhook.php_json_encode(webhook.build_payload("EE123456785KH", weight, unit))
            self.assertEqual(webhook.sign(body, SECRET), laravel_signature(body, SECRET))

    @unittest.skipUnless(shutil.which("php"), "php CLI not installed")
    def test_matches_real_php(self):
        """Run the exact Laravel middleware expression in PHP."""
        body = webhook.php_json_encode(webhook.build_payload("EE123456785KH", "2.35", "kg"))
        php = ("$b = stream_get_contents(STDIN);"
               "echo hash_hmac('sha256', json_encode(json_decode($b, true)), $argv[1]);")
        out = subprocess.run(["php", "-r", php, SECRET], input=body, capture_output=True, check=True)
        self.assertEqual(out.stdout.decode(), webhook.sign(body, SECRET))


class ConfigTests(unittest.TestCase):
    def test_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w") as f:
                f.write("# comment\nWEBHOOK_URL=https://example.test/api/webhook/notify\n"
                        "WEBHOOK_SECRET=\"abc def\"\nWEBHOOK_TIMEOUT=7\n")
            with mock.patch.dict(os.environ, {}, clear=True):
                cfg = webhook.WebhookConfig.from_env(webhook.load_env([path]))
        self.assertEqual((cfg.url, cfg.secret, cfg.timeout),
                         ("https://example.test/api/webhook/notify", "abc def", 7.0))

    def test_missing_values(self):
        with self.assertRaises(ValueError) as cm:
            webhook.WebhookConfig.from_env({"WEBHOOK_URL": "", "WEBHOOK_SECRET": ""})
        self.assertIn("WEBHOOK_SECRET", str(cm.exception))


class FakeResponse(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class SendTests(unittest.TestCase):
    cfg = webhook.WebhookConfig("https://example.test/api/webhook/notify", SECRET, 3)
    payload = webhook.build_payload("EE123456785KH", "2.35", "kg")

    def send_with(self, fake):
        return webhook.send(self.cfg, self.payload, opener=fake)

    def test_request_headers_and_body(self):
        seen = {}

        def fake(req, timeout):
            seen.update(req=req, timeout=timeout)
            return FakeResponse(200, b'{"code":200,"success":true,"message":"Webhook processed successfully","result":null}')

        r = self.send_with(fake)
        self.assertTrue(r.ok)
        req = seen["req"]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.get_header("Accept"), "application/json")
        self.assertEqual(req.get_header("Signature"), webhook.sign(req.data, SECRET))
        self.assertEqual(req.get_header("Signature"), laravel_signature(req.data, SECRET))
        self.assertEqual(seen["timeout"], 3)

    def test_2xx_without_success_is_not_success(self):
        r = self.send_with(lambda req, timeout: FakeResponse(200, b"<html>ok</html>"))
        self.assertFalse(r.ok)
        r = self.send_with(lambda req, timeout: FakeResponse(200, b'{"success":false,"message":"x"}'))
        self.assertFalse(r.ok)

    def test_http_error(self):
        def fake(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {},
                                         io.BytesIO(b'{"message":"Invalid Signature"}'))
        r = self.send_with(fake)
        self.assertFalse(r.ok)
        self.assertEqual((r.status, r.message), (403, "Invalid Signature"))

    def test_network_errors(self):
        for exc in (urllib.error.URLError("Name or service not known"), TimeoutError(),
                    ConnectionResetError("reset")):
            def fake(req, timeout, exc=exc):
                raise exc
            r = self.send_with(fake)
            self.assertFalse(r.ok)
            self.assertIsNone(r.status)

    def test_secret_never_echoed(self):
        r = self.send_with(lambda req, timeout: FakeResponse(500, f"debug {SECRET}\x1b[31m".encode()))
        self.assertNotIn(SECRET, r.body)
        self.assertNotIn("\x1b", r.body)


class LocalServerTest(unittest.TestCase):
    """End-to-end over real HTTP to a local server that verifies like Laravel."""

    def test_round_trip(self):
        received = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                ok = hmac.compare_digest(self.headers.get("signature", ""),
                                         laravel_signature(raw, SECRET))
                received.update(body=json.loads(raw), ok=ok)
                code = 200 if ok else 403
                msg = b'{"success":true,"message":"Webhook processed successfully"}' if ok \
                    else b'{"success":false,"message":"Invalid Signature"}'
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(msg)

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/webhook/notify"
            good = webhook.send(webhook.WebhookConfig(url, SECRET, 5), SendTests.payload)
            bad = webhook.send(webhook.WebhookConfig(url, "wrong", 5), SendTests.payload)
        finally:
            server.shutdown()
        self.assertTrue(good.ok, good.message)
        self.assertEqual(received["body"][0]["WEIGHT"], 2.35)
        self.assertFalse(bad.ok)
        self.assertEqual(bad.status, 403)


if __name__ == "__main__":
    unittest.main()
