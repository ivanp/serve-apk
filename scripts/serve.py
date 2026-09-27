#!/usr/bin/env python3
"""
HTTP server for serving Android APK with auto-shutdown after download.
Zero external dependencies (standard library only).
"""

import argparse
import os
import re
import signal
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse


def get_ip_addresses():
    """Discover local LAN, Tailscale, and loopback IP addresses."""
    ips = {"lan": [], "tailscale": [], "local": ["127.0.0.1"]}

    # Try route probing with UDP socket (fastest, most accurate for default route)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip.startswith("100."):
            ips["tailscale"].append(primary_ip)
        elif not primary_ip.startswith("127."):
            ips["lan"].append(primary_ip)
    except Exception:
        pass

    # Try hostname lookup for additional interfaces
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip.startswith("127."):
                continue
            elif ip.startswith("100.") and ip not in ips["tailscale"]:
                ips["tailscale"].append(ip)
            elif ip not in ips["lan"]:
                ips["lan"].append(ip)
    except Exception:
        pass

    return ips


def find_free_port(preferred_port=8080):
    """Find preferred_port or next available port within valid TCP range (1-65535)."""
    if not (1 <= preferred_port <= 65535):
        preferred_port = 8080

    max_port = min(preferred_port + 50, 65536)
    for port in range(preferred_port, max_port):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free ports found in range {preferred_port}-{max_port - 1}")


class ApkRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, target_apk_name=None, target_apk_path=None, shutdown_callback=None, **kwargs):
        self.target_apk_name = target_apk_name
        self.target_apk_path = target_apk_path
        self.shutdown_callback = shutdown_callback
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {format % args}\n")

    def sanitize_filename(self, filename):
        """Sanitize filename to prevent header injection in Content-Disposition."""
        safe_name = os.path.basename(filename)
        safe_name = re.sub(r'[^a-zA-Z0-9._-]', '_', safe_name)
        return safe_name or "app.apk"

    def do_HEAD(self):
        parsed_path = unquote(urlparse(self.path).path).strip("/")
        if parsed_path != self.target_apk_name:
            self.send_error(404, "Not Found")
            return
        super().do_HEAD()

    def do_GET(self):
        # Strict routing: only serve the target APK file, disallow all other files and directory listings
        parsed_path = unquote(urlparse(self.path).path).strip("/")

        if parsed_path != self.target_apk_name:
            self.send_error(404, "Not Found")
            return

        client_ip = self.client_address[0]

        # Trigger download callback when transfer begins
        if self.shutdown_callback:
            self.shutdown_callback(client_ip)

        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.log_message("Client %s disconnected during APK download", client_ip)

    def end_headers(self):
        # Only attach APK download headers if this is a 200/206 response for the target APK
        parsed_path = unquote(urlparse(self.path).path).strip("/")
        if parsed_path == self.target_apk_name and getattr(self, "_headers_buffer", None):
            status_line = self._headers_buffer[0].decode("latin-1") if self._headers_buffer else ""
            if " 200 " in status_line or " 206 " in status_line:
                safe_filename = self.sanitize_filename(self.target_apk_name)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Disposition", f'attachment; filename="{safe_filename}"')
        super().end_headers()


class ApkServerManager:
    def __init__(self, apk_path, port=8080, shutdown_delay=300, idle_timeout=900):
        self.apk_path = os.path.abspath(apk_path)
        if not os.path.isfile(self.apk_path):
            raise FileNotFoundError(f"APK file not found: {self.apk_path}")

        self.directory = os.path.dirname(self.apk_path)
        self.apk_name = os.path.basename(self.apk_path)
        self.apk_size_mb = os.path.getsize(self.apk_path) / (1024 * 1024)
        self.preferred_port = max(1, min(port, 65535))
        self.shutdown_delay = max(0, shutdown_delay)
        self.idle_timeout = max(0, idle_timeout)

        self.server = None
        self.port = None
        self.timer = None
        self.idle_timer = None
        self.shutdown_triggered = False
        self._lock = threading.Lock()

    def on_download_started(self, client_ip):
        with self._lock:
            if self.shutdown_triggered:
                return
            self.shutdown_triggered = True

            print(f"\n[serve-apk] Download started by {client_ip} for '{self.apk_name}'")
            print(f"[serve-apk] Auto-shutdown timer started: server will terminate in {self.shutdown_delay}s ({self.shutdown_delay // 60}m).")

            if self.idle_timer:
                self.idle_timer.cancel()
                self.idle_timer = None

            if self.shutdown_delay > 0:
                self.timer = threading.Timer(self.shutdown_delay, self._shutdown_server)
                self.timer.daemon = True
                self.timer.start()
            else:
                self._shutdown_server()

    def _shutdown_server(self):
        print("\n[serve-apk] Auto-shutdown timer expired. Stopping HTTP server.")
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _idle_timeout_reached(self):
        print(f"\n[serve-apk] Idle timeout of {self.idle_timeout}s reached with no downloads. Stopping HTTP server.")
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _setup_signal_handlers(self):
        def handle_sigterm(signum, frame):
            print("\n[serve-apk] Received termination signal (SIGTERM). Stopping HTTP server.")
            if self.server:
                threading.Thread(target=self.server.shutdown, daemon=True).start()

        try:
            signal.signal(signal.SIGTERM, handle_sigterm)
        except (ValueError, AttributeError):
            pass

    def start(self):
        self.port = find_free_port(self.preferred_port)

        def handler_factory(*args, **kwargs):
            return ApkRequestHandler(
                *args,
                directory=self.directory,
                target_apk_name=self.apk_name,
                target_apk_path=self.apk_path,
                shutdown_callback=self.on_download_started,
                **kwargs
            )

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), handler_factory)
        self.server.daemon_threads = True

        self._setup_signal_handlers()

        if self.idle_timeout > 0:
            self.idle_timer = threading.Timer(self.idle_timeout, self._idle_timeout_reached)
            self.idle_timer.daemon = True
            self.idle_timer.start()

        ips = get_ip_addresses()

        print("=" * 60)
        print(f"  APK READY: {self.apk_name} ({self.apk_size_mb:.1f} MB)")
        print("=" * 60)
        print(f"  Localhost:  http://localhost:{self.port}/{self.apk_name}")
        for ip in ips["lan"]:
            print(f"  LAN (WiFi): http://{ip}:{self.port}/{self.apk_name}")
        for ip in ips["tailscale"]:
            print(f"  Tailscale:  http://{ip}:{self.port}/{self.apk_name}")
        print("-" * 60)
        print(f"  • Auto-shutdown: {self.shutdown_delay // 60}m ({self.shutdown_delay}s) after download starts")
        print(f"  • Idle timeout:  {self.idle_timeout // 60}m ({self.idle_timeout}s) if no download occurs")
        print(f"  • Stop manually: Ctrl+C")
        print("=" * 60 + "\n")
        sys.stdout.flush()

        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve-apk] Interrupted by user. Shutting down.")
        finally:
            with self._lock:
                if self.timer:
                    self.timer.cancel()
                if self.idle_timer:
                    self.idle_timer.cancel()
            self.server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Serve APK over HTTP with auto-shutdown on download.")
    parser.add_argument("apk", help="Path to .apk file")
    parser.add_argument("--port", type=int, default=8080, help="Preferred port (default: 8080)")
    parser.add_argument("--delay", type=int, default=300, help="Shutdown delay in seconds after download (default: 300s / 5m)")
    parser.add_argument("--idle", type=int, default=900, help="Idle timeout in seconds (default: 900s / 15m)")

    args = parser.parse_args()

    manager = ApkServerManager(
        apk_path=args.apk,
        port=args.port,
        shutdown_delay=args.delay,
        idle_timeout=args.idle
    )
    manager.start()


if __name__ == "__main__":
    main()
