#!/usr/bin/env python3
"""
HTTP server for serving Android APK with auto-shutdown after download.
Zero external dependencies (standard library only).
"""

import argparse
import os
import socket
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


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
    """Find preferred_port or next available port."""
    for port in range(preferred_port, preferred_port + 50):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No free ports found starting from {preferred_port}")


class ApkRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, target_apk_name=None, shutdown_callback=None, **kwargs):
        self.target_apk_name = target_apk_name
        self.shutdown_callback = shutdown_callback
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {format % args}\n")

    def end_headers(self):
        if self.path.endswith(".apk"):
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(self.path)}"')
        super().end_headers()

    def do_GET(self):
        clean_path = self.path.strip("/").split("?")[0]
        is_apk_request = clean_path == self.target_apk_name or clean_path.endswith(".apk")
        client_ip = self.client_address[0]

        super().do_GET()

        if is_apk_request:
            if hasattr(self, "shutdown_callback") and self.shutdown_callback:
                self.shutdown_callback(client_ip)


class ApkServerManager:
    def __init__(self, apk_path, port=8080, shutdown_delay=300, idle_timeout=900):
        self.apk_path = os.path.abspath(apk_path)
        if not os.path.isfile(self.apk_path):
            raise FileNotFoundError(f"APK file not found: {self.apk_path}")

        self.directory = os.path.dirname(self.apk_path)
        self.apk_name = os.path.basename(self.apk_path)
        self.apk_size_mb = os.path.getsize(self.apk_path) / (1024 * 1024)
        self.preferred_port = port
        self.shutdown_delay = shutdown_delay
        self.idle_timeout = idle_timeout

        self.server = None
        self.port = None
        self.timer = None
        self.idle_timer = None
        self.shutdown_triggered = False

    def on_download_started(self, client_ip):
        if self.shutdown_triggered:
            return

        print(f"\n[serve-apk] Download detected from {client_ip} for '{self.apk_name}'")
        print(f"[serve-apk] Auto-shutdown countdown started: server will stop in {self.shutdown_delay}s ({self.shutdown_delay // 60} minutes).")
        self.shutdown_triggered = True

        if self.idle_timer:
            self.idle_timer.cancel()

        self.timer = threading.Timer(self.shutdown_delay, self._shutdown_server)
        self.timer.daemon = True
        self.timer.start()

    def _shutdown_server(self):
        print("\n[serve-apk] Auto-shutdown timer expired. Shutting down HTTP server cleanly.")
        if self.server:
            threading.Thread(target=self.server.shutdown).start()

    def _idle_timeout_reached(self):
        print(f"\n[serve-apk] Idle timeout of {self.idle_timeout}s reached with no downloads. Shutting down.")
        if self.server:
            threading.Thread(target=self.server.shutdown).start()

    def start(self):
        self.port = find_free_port(self.preferred_port)

        def handler_factory(*args, **kwargs):
            return ApkRequestHandler(
                *args,
                directory=self.directory,
                target_apk_name=self.apk_name,
                shutdown_callback=self.on_download_started,
                **kwargs
            )

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), handler_factory)

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
        print(f"  • Auto-shutdown: 5 minutes after first download")
        print(f"  • Idle timeout:  {self.idle_timeout // 60} minutes if no download occurs")
        print(f"  • Stop manually: Ctrl+C")
        print("=" * 60 + "\n")
        sys.stdout.flush()

        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            print("\n[serve-apk] Interrupted by user. Shutting down.")
        finally:
            if self.timer:
                self.timer.cancel()
            if self.idle_timer:
                self.idle_timer.cancel()
            self.server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Serve APK over HTTP with 5-minute auto-shutdown on download.")
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
