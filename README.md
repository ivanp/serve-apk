# serve-apk

A Claude agent skill to build Android APKs using Gradle and serve them over a local HTTP server with automatic 5-minute shutdown upon download.

## Features

- **Automatic Gradle Build**: Runs `./gradlew assembleDebug` (or custom build variant)
- **Zero External Dependencies**: Standard library Python (`http.server`, `threading`, `socket`)
- **Multi-Network Discovery**: Automatically displays Localhost, Local Wi-Fi / LAN, and Tailscale IP URLs
- **Auto-Shutdown**: Starts a 5-minute countdown as soon as the APK is downloaded
- **Idle Timeout**: Automatically shuts down after 15 minutes if never downloaded
- **Port Conflict Resolution**: Automatically finds the next free port if 8080 is in use

## Installation

Copy or symlink into your agent skills directory:

```bash
mkdir -p ~/.agents/skills
cp -r . ~/.agents/skills/serve-apk
```

## Usage

```bash
python3 scripts/serve.py app/build/outputs/apk/debug/app-debug.apk
```
