---
name: serve-apk
description: Builds an Android APK using Gradle and serves it over a local HTTP server with automatic shutdown 5 minutes after download. Use this skill whenever the user asks to build an APK, serve an APK over HTTP, share or download an Android build to a physical device, or test an APK on local Wi-Fi / Tailscale.
---

# Serve APK

Builds an Android project's APK and hosts it on a lightweight, zero-dependency HTTP server that automatically terminates 5 minutes after the first download.

## Workflow

### 1. Build the APK

In the Android project root (or root of the repository containing `gradlew`):

```bash
./gradlew assembleDebug
```

*Note: If the user requests a specific flavor or build type (e.g. `assembleRelease`, `assembleStagingDebug`), use that task instead.*

Locate the generated `.apk` file:
- Standard location: `app/build/outputs/apk/<variant>/<app-name>-<variant>.apk`
- If multiple modules exist, look under `<module>/build/outputs/apk/`

### 2. Start the Server

Run the bundled `scripts/serve.py` with the path to the APK:

```bash
python3 <skill-path>/scripts/serve.py <path-to-apk>
```

#### Optional Flags:
- `--port <port>`: Preferred port (default: `8080`). If occupied, the script automatically binds the next free port.
- `--delay <seconds>`: Shutdown delay after first download in seconds (default: `300` / 5 minutes).
- `--idle <seconds>`: Idle timeout if no download occurs (default: `900` / 15 minutes).

### 3. Display Download Links

The server outputs formatted URLs for every detected interface:
- **LAN / Wi-Fi IP**: `http://<lan-ip>:<port>/<apk-name>.apk` (for physical phones on the same Wi-Fi)
- **Localhost**: `http://localhost:<port>/<apk-name>.apk` (for emulators or local browser)
- **Tailscale IP**: `http://<tailscale-ip>:<port>/<apk-name>.apk` (if Tailscale network is active)

Always present these links directly in the response so the user can immediately click or scan them on their device.

### 4. Lifecycle & Auto-Shutdown

1. **Idle state**: Server waits for incoming GET requests.
2. **Download detected**: When a client requests the `.apk` file, a 5-minute (300s) countdown timer begins.
3. **Shutdown**: Once the timer expires, the server gracefully closes sockets and exits.
4. **Fallback**: If no download occurs within 15 minutes (900s), the server terminates automatically.
5. **Manual Stop**: The server can be stopped at any time with `Ctrl+C` or by terminating its process.
