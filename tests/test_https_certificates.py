# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Regression for an empty Python CA store on a first-time macOS installation.

Only a local TLS server and generated, temporary certificates are used. Real TLS
handshakes prove that the fix trusts the intended CA while rejecting an unknown
issuer and the wrong hostname. No GitHub or machine credentials are used.
"""
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.support import TempHome, load_app, runtime

hub = load_app("hub_https_tests", "tools/hub/app.py")


def empty_context():
    return ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class TestCertificateSelection(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ))
        for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
            os.environ.pop(key, None)

    def test_macos_roots_work_with_the_reported_empty_python_store(self):
        if not Path(runtime.MACOS_CA_FILE).is_file():
            self.skipTest("macOS system bundle not available")
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.ssl, "create_default_context", side_effect=empty_context), \
                patch.object(runtime.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath=None)):
            ctx = runtime.https_context()
        self.assertGreater(ctx.cert_store_stats()["x509_ca"], 0)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)

    def test_custom_configuration_and_other_platforms_are_preserved(self):
        for platform, env, capath in [("win32", {}, None), ("linux", {}, None),
                                     ("darwin", {"SSL_CERT_FILE": "/explicit/missing.pem"}, None),
                                     ("darwin", {"SSL_CERT_DIR": "/explicit/certs"}, None),
                                     ("darwin", {}, "/lazy/certs")]:
            with self.subTest(platform=platform, env=env, capath=capath), \
                    patch.dict(os.environ, env), patch.object(runtime.sys, "platform", platform), \
                    patch.object(runtime.ssl, "create_default_context", side_effect=empty_context), \
                    patch.object(runtime.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath=capath)), \
                    patch.object(runtime, "MACOS_CA_FILE", "/must-not-be-read"):
                ctx = runtime.https_context()
                self.assertEqual(ctx.cert_store_stats()["x509_ca"], 0)
                self.assertTrue(ctx.check_hostname)
                self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_missing_os_bundle_fails_closed_with_a_certificate_error(self):
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.ssl, "create_default_context", side_effect=empty_context), \
                patch.object(runtime.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath=None)), \
                patch.object(runtime, "MACOS_CA_FILE", "/missing-test-ca.pem"):
            with self.assertRaises(ssl.SSLCertVerificationError):
                runtime.https_context()

    def test_existing_roots_are_kept(self):
        if not Path(runtime.MACOS_CA_FILE).is_file():
            self.skipTest("system bundle not available")
        ctx = empty_context()
        ctx.load_verify_locations(runtime.MACOS_CA_FILE)
        count = ctx.cert_store_stats()
        with patch.object(runtime.sys, "platform", "darwin"), \
                patch.object(runtime.ssl, "create_default_context", return_value=ctx), \
                patch.object(runtime, "MACOS_CA_FILE", "/must-not-be-read"):
            self.assertIs(runtime.https_context(), ctx)
        self.assertEqual(ctx.cert_store_stats(), count)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/fail":
            self.send_response(403)
        else:
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"login":"tester","id":1}')

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"device_code":"test-device","user_code":"TEST-CODE","verification_uri":"https://example.invalid/device","interval":5,"expires_in":900}')


@unittest.skipUnless(shutil.which("openssl"), "openssl is needed for temporary TLS fixtures")
class TestVerifiedHTTPS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        config = cls.root / "openssl.cnf"
        config.write_text("[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
                          "[dn]\nCN=localhost\n[ext]\nbasicConstraints=critical,CA:TRUE\n"
                          "keyUsage=critical,digitalSignature,keyCertSign,keyEncipherment\n"
                          "subjectAltName=DNS:localhost\n")
        for name in ("server", "other"):
            subprocess.run([shutil.which("openssl"), "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-days", "1", "-config", str(config), "-keyout", str(cls.root / (name + ".key")),
                            "-out", str(cls.root / (name + ".pem"))], check=True, capture_output=True)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(cls.root / "server.pem", cls.root / "server.key")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.socket = server_ctx.wrap_socket(cls.server.socket, server_side=True)
        cls.addClassCleanup(cls.server.server_close)
        cls.addClassCleanup(cls.server.shutdown)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = "https://localhost:%s" % cls.server.server_address[1]

    def setUp(self):
        self.enterContext(TempHome())
        self.enterContext(patch.dict(os.environ))
        for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE"):
            os.environ.pop(key, None)
        self.enterContext(patch.object(runtime.sys, "platform", "darwin"))
        self.enterContext(patch.object(runtime.ssl, "create_default_context", side_effect=empty_context))
        self.enterContext(patch.object(runtime.ssl, "get_default_verify_paths", return_value=SimpleNamespace(capath=None)))
        self.enterContext(patch.object(runtime, "MACOS_CA_FILE", str(self.root / "server.pem")))

    def test_empty_default_store_fails_but_tool_request_succeeds(self):
        with self.assertRaises(urllib.error.URLError) as err:
            runtime.urllib.request.urlopen(self.url, context=empty_context(), timeout=3)
        self.assertTrue(runtime.is_certificate_error(err.exception))
        status, raw, kind = runtime.request(self.url, timeout=3)
        self.assertEqual((status, json.loads(raw)["login"], kind), (200, "tester", "application/json"))

    def test_device_code_and_download_share_verified_transport(self):
        result = runtime.post_form(self.url, {"client_id": "test"}, timeout=3)
        self.assertEqual(result["user_code"], "TEST-CODE")
        dest = runtime.home() / "download.json"
        runtime.download(self.url, dest, timeout=3)
        self.assertEqual(json.loads(dest.read_bytes())["id"], 1)

    def test_unknown_issuer_is_rejected_and_download_not_written(self):
        with patch.object(runtime, "MACOS_CA_FILE", str(self.root / "other.pem")):
            for call in (lambda: runtime.request(self.url, timeout=3),
                         lambda: runtime.post_form(self.url, {}, timeout=3),
                         lambda: runtime.download(self.url, runtime.home() / "blocked", timeout=3)):
                with self.assertRaises(urllib.error.URLError) as err:
                    call()
                self.assertTrue(runtime.is_certificate_error(err.exception))
        self.assertFalse((runtime.home() / "blocked").exists())

    def test_wrong_hostname_is_rejected(self):
        with self.assertRaises(urllib.error.URLError) as err:
            runtime.request(self.url.replace("localhost", "127.0.0.1"), timeout=3)
        self.assertTrue(runtime.is_certificate_error(err.exception))

    def test_http_error_semantics_are_unchanged(self):
        self.assertEqual(runtime.request(self.url + "/fail", timeout=3)[0], 403)

    def test_hub_button_obtains_code_over_verified_tls_without_saved_account(self):
        mgr = hub.Session().account
        with patch.object(hub, "DEVICE_CODE_URL", self.url), \
                patch.object(hub, "oauth_client_id", return_value="test-client"), \
                patch.object(mgr, "_poll"):
            result = mgr.start()
        self.assertEqual(result["userCode"], "TEST-CODE")
        self.assertTrue(mgr.waiting)

    def test_hub_reports_certificate_failure_instead_of_offline(self):
        mgr = hub.Session().account
        with patch.object(hub, "DEVICE_CODE_URL", self.url.replace("localhost", "127.0.0.1")), \
                patch.object(hub, "oauth_client_id", return_value="test-client"):
            with self.assertRaisesRegex(RuntimeError, "HTTPS certificate"):
                mgr.start()
        self.assertFalse(mgr.waiting)

    def test_certificate_failure_during_poll_stops_without_waiting_for_expiry(self):
        mgr = hub.Session().account
        mgr.waiting = True
        with patch.object(hub, "ACCESS_TOKEN_URL", self.url.replace("localhost", "127.0.0.1")):
            mgr._poll("test-client", "test-device", 0, 2)
        self.assertFalse(mgr.waiting)
        self.assertIn("HTTPS certificate", mgr.error)

    def test_other_connection_failure_keeps_the_offline_message(self):
        with patch.object(hub, "oauth_client_id", return_value="test-client"), \
                patch.object(runtime, "post_form", side_effect=urllib.error.URLError("test-offline")):
            with self.assertRaisesRegex(RuntimeError, "could not be reached"):
                hub.Session().account.start()


class TestPublicCertificateReport(unittest.TestCase):
    def test_report_allowlist_excludes_secrets_paths_and_untrusted_versions(self):
        secret = 'PRIVATE-SENTINEL-user-token-path'
        with patch.dict(os.environ, {'SSL_CERT_FILE': '/private/' + secret,
                                     'SSL_CERT_DIR': '/private/' + secret,
                                     'GITHUB_TOKEN': secret}), \
                patch.object(runtime, 'running_hub_tag', return_value='hub-v1.3.2\n' + secret), \
                patch.object(runtime.ssl.SSLContext, 'load_default_certs', side_effect=OSError(secret)), \
                patch.object(runtime.urllib.request, 'urlopen', side_effect=AssertionError('network forbidden')), \
                patch.object(runtime.subprocess, 'run', side_effect=AssertionError('process forbidden')):
            report = runtime.certificate_diagnostics()
        self.assertNotIn(secret, report)
        self.assertNotIn('/private/', report)
        facts = dict(line.split(': ', 1) for line in report.splitlines()[1:-1])
        self.assertEqual(set(facts), {'report', 'error', 'os', 'python', 'hub',
                                     'custom_ca_file', 'custom_ca_directory',
                                     'default_ca_count', 'default_ca_directory',
                                     'tool_ca_count', 'tool_ca_load'})
        self.assertEqual(facts['hub'], 'unknown')
        self.assertEqual(facts['custom_ca_file'], 'set')
        self.assertEqual(facts['default_ca_count'], 'unknown')
        self.assertEqual(facts['tool_ca_load'], 'failed')
        self.assertRegex(facts['python'], r'^\d+\.\d+\.\d+$')

    def test_report_does_not_create_ssl_key_log_or_mutate_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            keylog = Path(tmp) / 'must-not-exist'
            with patch.dict(os.environ, {'SSLKEYLOGFILE': str(keylog)}):
                before = dict(os.environ)
                runtime.certificate_diagnostics()
                self.assertEqual(dict(os.environ), before)
            self.assertFalse(keylog.exists())

    def test_report_uses_numeric_release_and_macos_fallback_counts(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(runtime.sys, 'platform', 'darwin'), \
                patch.object(runtime, 'running_hub_tag', return_value='hub-v1.3.2'), \
                patch.object(runtime.ssl.SSLContext, 'load_default_certs'), \
                patch.object(runtime.ssl, 'get_default_verify_paths', return_value=SimpleNamespace(capath=None)):
            if not Path(runtime.MACOS_CA_FILE).is_file():
                self.skipTest('macOS root bundle unavailable')
            report = runtime.certificate_diagnostics()
        self.assertIn('hub: hub-v1.3.2\n', report)
        self.assertIn('default_ca_count: 0\n', report)
        self.assertRegex(report, r'tool_ca_count: [1-9][0-9]*\n')
        self.assertIn('tool_ca_load: ok\n', report)

    def test_only_certificate_failures_generate_report_and_retry_clears_it(self):
        with TempHome(), patch.object(hub, 'oauth_client_id', return_value='test-client'):
            mgr = hub.Session().account
            failure = urllib.error.URLError(ssl.SSLCertVerificationError('PRIVATE-SENTINEL'))
            with patch.object(runtime, 'post_form', side_effect=failure), \
                    patch.object(runtime, 'certificate_diagnostics', return_value='safe-report') as diagnose:
                with self.assertRaises(RuntimeError):
                    mgr.start()
                diagnose.assert_called_once_with()
            self.assertEqual(mgr.certificate_report, 'safe-report')
            self.assertNotIn('PRIVATE-SENTINEL', mgr.error)
            with patch.object(runtime, 'post_form', side_effect=urllib.error.URLError('offline')), \
                    patch.object(runtime, 'certificate_diagnostics') as diagnose:
                with self.assertRaises(RuntimeError):
                    mgr.start()
                diagnose.assert_not_called()
            self.assertIsNone(mgr.certificate_report)
            with patch.object(runtime, 'post_form', side_effect=failure), \
                    patch.object(runtime, 'certificate_diagnostics', return_value='safe-poll-report'):
                mgr.waiting = True
                mgr._poll('client', 'PRIVATE-DEVICE-CODE', 0, 2)
            self.assertFalse(mgr.waiting)
            self.assertEqual(mgr.certificate_report, 'safe-poll-report')
