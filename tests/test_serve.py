import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from scripts.serve import ApkRequestHandler, ApkServerManager, find_free_port


class TestServeApk(unittest.TestCase):
    def test_find_free_port_bounds(self):
        # High port should clamp and not throw OverflowError
        port = find_free_port(65530)
        self.assertTrue(65530 <= port <= 65535)

        # Invalid ports fall back to default
        port_invalid = find_free_port(99999)
        self.assertTrue(1 <= port_invalid <= 65535)

    def test_sanitize_filename(self):
        handler = ApkRequestHandler.__new__(ApkRequestHandler)
        self.assertEqual(handler.sanitize_filename("app-debug.apk"), "app-debug.apk")
        self.assertEqual(handler.sanitize_filename("app;evil=1.apk"), "app_evil_1.apk")
        self.assertEqual(handler.sanitize_filename("path/to/my_app.apk"), "my_app.apk")

    def test_request_routing_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            apk_path = os.path.join(tmpdir, "test-app.apk")
            secret_path = os.path.join(tmpdir, "secret.txt")

            with open(apk_path, "wb") as f:
                f.write(b"PK\x03\x04fake apk content")
            with open(secret_path, "wb") as f:
                f.write(b"sensitive data")

            callback_called = []

            def callback(client_ip):
                callback_called.append(client_ip)

            def handler_factory(*args, **kwargs):
                return ApkRequestHandler(
                    *args,
                    directory=tmpdir,
                    target_apk_name="test-app.apk",
                    target_apk_path=apk_path,
                    shutdown_callback=callback,
                    **kwargs,
                )

            port = find_free_port(8900)
            server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory)
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()

            try:
                base_url = f"http://127.0.0.1:{port}"

                # 1. Target APK should return 200 with APK headers
                req = urllib.request.Request(f"{base_url}/test-app.apk")
                with urllib.request.urlopen(req) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertEqual(resp.headers.get("Content-Type"), "application/vnd.android.package-archive")
                    self.assertEqual(resp.headers.get("Content-Disposition"), 'attachment; filename="test-app.apk"')
                    self.assertEqual(resp.read(), b"PK\x03\x04fake apk content")

                self.assertEqual(len(callback_called), 1)

                # 2. Other files in same directory should return 404 (isolation)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"{base_url}/secret.txt")
                self.assertEqual(ctx.exception.code, 404)

                # 3. Root listing should return 404 (no directory listing)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"{base_url}/")
                self.assertEqual(ctx.exception.code, 404)

                # 4. Non-existent .apk request should return 404 and NOT trigger callback
                initial_callback_count = len(callback_called)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(f"{base_url}/nonexistent.apk")
                self.assertEqual(ctx.exception.code, 404)
                self.assertEqual(len(callback_called), initial_callback_count)

            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
