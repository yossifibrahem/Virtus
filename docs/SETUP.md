# Installation and builds

[Showcase](../README.md) · [Configuration](CONFIGURATION.md)

Desktop users need Docker Desktop and an OpenAI-compatible model provider. The first sandbox installation needs internet access. API-provider charges, if any, are separate.

## Windows x64 Installation

The Windows build is a portable ZIP. Windows builds are unsigned. It contains the complete
PyInstaller `onedir` application; keep the extracted folder together.

1. Install Docker Desktop for Windows and allow its normal setup to finish.
2. Download `Virtus-AI-Chat-<version>-windows-x64.zip` and its `.sha256` file from [GitHub Releases](https://github.com/yossifibrahem/lumen-ai-chat/releases).
3. Verify the checksum, then extract the entire ZIP to a writable local folder.
4. Run `Virtus AI Chat.exe` from the extracted `Virtus AI Chat` folder. Windows
   may show an unsigned-application warning on first launch.
5. If Docker is stopped, use **Docker Status** in the tray and click **Start
   Docker** in Virtus's setup page. When prompted, click **Install Virtus Tools**.

Virtus remains in the notification area with **Open Virtus**, **Docker Status**,
**Open Logs**, and **Quit Virtus** actions. Quit stops the local listener, MCP
sessions, and running Virtus containers before removing the tray icon. Cleanup
has a single 15-second deadline; stopped containers remain available for fast
reuse. Chats stay under `%USERPROFILE%\.virtus`; workspaces live in Docker named volumes.

The first sandbox installation needs an internet connection. The app includes its Python runtime, and Docker installs the sandbox runtimes and packages. MCP servers are configured separately.

## macOS Installation

The macOS desktop build targets **Apple Silicon Macs running macOS 14 or newer**. It opens Virtus in the default browser and keeps the local server available from a small menu-bar item; it does not install Python or Node.js on the Mac.

1. Install and start [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Download the Apple Silicon DMG from [GitHub Releases](https://github.com/yossifibrahem/lumen-ai-chat/releases).
3. Open the DMG and drag **Virtus AI Chat** into **Applications**.
4. Follow the release notes for first-launch instructions. Builds may be unsigned or unnotarized.
5. Virtus opens its setup page. If Docker is stopped, click **Start Docker**. When prompted, click **Install Virtus Tools**. Docker pulls `ubuntu:24.04`, installs Python, Node.js, and the sandbox packages inside it, and creates `virtus-sandbox:latest` locally.

Virtus remains in the menu bar using the bundled Virtus artwork, with **Open Virtus**, **Docker Status**, **Open Logs**, and **Quit Virtus** actions. Application updates are manual: replace the old app with a newer DMG. Chats stay under `~/.virtus/` and workspaces live in Docker named volumes. Both persist across app updates and uninstall.

Internet access is required during the first sandbox installation for the Ubuntu image and package repositories. No MCP server source is fetched or compiled by this build.

### Access from another device (macOS)

In the Virtus menu bar menu, select **Allow local network access**. Virtus restarts
its web listener and shows the address to open on another device connected to the
same network, such as `http://192.168.1.5:38492`. Use **Show network address** to
view it again. Allow incoming connections if macOS asks, and keep your Mac awake.

This shares access to your chats, files, settings, and tools with devices on that
network; use a trusted network. The switch is off by default, and your choice is
saved in `~/.virtus/desktop-network.json` for future launches. Turn it off to return
to access from this Mac only. Browser sessions are preserved when switching.

Copy buttons use a compatibility fallback on local-network HTTP connections.
If your browser blocks copying, select the text and copy it manually.

To uninstall, quit Virtus and move `Virtus AI Chat.app` to Trash. Remove `~/.virtus/` separately only if you also want to permanently delete chats, settings, and memory. Delete workspace volumes separately in Docker Desktop. Docker images can be removed separately through Docker Desktop.

## Developer Quick Start

Use Python 3.10+ (3.12 recommended) and Docker running Linux containers.
Desktop users install Docker Desktop and connect a model provider.

```bash
# 1. Clone
git clone https://github.com/yossifibrahem/lumen-ai-chat.git
cd lumen-ai-chat

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Build the Docker sandbox image (required for native and MCP tools)
identity="$(python -c 'import build_info; print(build_info.sandbox_identity())')"
docker build --build-arg "VIRTUS_SANDBOX_IDENTITY=$identity" -f Dockerfile.sandbox -t virtus-sandbox:latest .

# 5. Start the app
python app.py
```

Open **http://localhost:8080**, then open the settings panel to enter your API key, base URL, and model name.

The development server binds to `127.0.0.1` with debug mode disabled by default. For deliberate LAN testing on a trusted network, set `VIRTUS_HOST=0.0.0.0`, configure `VIRTUS_ALLOWED_HOSTS` and `VIRTUS_CORS_ORIGINS`, and open `http://<your-computer-ip>:8080` from the other device.

> Source and desktop runs use the local `virtus-sandbox:latest` image. The desktop app contains the complete Docker build context, so first-run setup needs Docker Desktop but no host Python, Node.js, Git, or source checkout.

### Configuring tools

The built-in tools `bash`, `view`, `write`, and `edit` are provided by Virtus itself, with server-owned schemas and container-only execution. They work without an MCP server. Optional MCP servers provide additional tools; their code and dependencies must be available inside the container. Node.js, Python, `uv`, and `mcp-remote` are bundled for these integrations. MCP discovery returns only additional tools, while native tools are injected directly into each chat request.

## Building and deploying

### Build the Sandbox Image

All MCP servers run inside the active chat or folder's Docker sandbox. Build the image once before starting the app:

```bash
identity="$(python -c 'import build_info; print(build_info.sandbox_identity())')"
docker build --build-arg "VIRTUS_SANDBOX_IDENTITY=$identity" -f Dockerfile.sandbox -t virtus-sandbox:latest .
```

In PowerShell, compute the same value with `$identity = python -c "import
build_info; print(build_info.sandbox_identity())"`, then run the Docker command
with `--build-arg "VIRTUS_SANDBOX_IDENTITY=$identity"`.

The default is the locally built `virtus-sandbox:latest` image. The packaged
app embeds `Dockerfile.sandbox` and installs its runtimes and packages
inside Ubuntu during first-run setup. A content identity derived from
`.dockerignore`, `Dockerfile.sandbox`, `sandbox_tools.py`, and `sandbox_files.py` determines whether
the image needs rebuilding, so rebuilding or upgrading desktop-only code does
not invalidate it. The image remains configurable through
`VIRTUS_SANDBOX_IMAGE` or advanced settings.

### Build the macOS DMG

On an Apple Silicon Mac running macOS 14 or newer, use Python 3.12 (the
release workflow pins this version so the frozen runtime keeps the macOS 14
deployment target):

```bash
python -m pip install -r requirements-dev.txt -r requirements-desktop.txt
VIRTUS_BUILD_VERSION=0.2.0 packaging/build_macos.sh
```

The script resizes the Virtus logo into an `.icns`, generates Finder bundle/build versions from `VIRTUS_BUILD_VERSION`, freezes the one-process Waitress/menu-bar app, ad-hoc signs it when no identity is configured, and creates `dist/Virtus-AI-Chat-<version>-apple-silicon.dmg` with a SHA-256 checksum. Set `MACOS_SIGN_IDENTITY` after importing a Developer ID certificate to produce a distribution-signed app.

The release workflow builds and smoke-tests the ARM64 sandbox without publishing it, runs the Python tests, and publishes the DMG. End users build the same pinned sandbox locally on first run, so no container registry account is required.

To verify the sandbox Python runtime and workspace mount, run `python packaging/smoke_sandbox.py --image virtus-sandbox:latest`.

### Build the Windows ZIP

On Windows x64, use an x64 Python 3.12 environment:

```powershell
python -m pip install -r requirements-dev.txt -r requirements-desktop.txt
$env:VIRTUS_BUILD_VERSION = "0.2.0"
.\packaging\build_windows.ps1
```

The script generates the icon and executable version resource, freezes the
windowed Waitress/tray host, and archives the complete `onedir` folder. It then
extracts the ZIP to a clean temporary directory and runs the frozen resource
and server smoke mode from that extraction. Successful output is
`dist\Virtus-AI-Chat-<version>-windows-x64.zip` plus a SHA-256 file.

### Production Deployment

For a single-process production-style deployment, use Gunicorn:

```bash
gunicorn -c gunicorn.conf.py "app:create_app()"
```

`gunicorn.conf.py` defaults to **one worker with multiple threads**. Active stream state (cancellation events, reattach buffers) is stored in process memory, so multiple worker processes are not supported until stream state is moved to shared storage.
