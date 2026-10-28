# Lipa for Windows users

[Back to README](../README.en.md) · [Slovenščina](windows.md)

**Lipa does not currently have a native Windows implementation.** Its desktop bridge
uses Unix sockets, file ownership checks and POSIX process control. Installing Python
or running the shell scripts in Git Bash does not remove those dependencies.

The following is a **proposed, experimental WSL2/WSLg setup**, not an end-to-end tested
installation. It does not establish official OpenAI support for running the Linux desktop
app in WSLg. For an existing working Linux installation, see
[connecting over SSH](#alternative-windows-browser-with-a-linux-host).

## What runs where

| Component | Environment |
| --- | --- |
| Lipa, Python and Codex CLI | The same WSL2 distribution and Linux user. |
| Codex/ChatGPT desktop app | **The Linux version inside that WSL2 distribution**, opened through WSLg. |
| Voice interface and microphone | Windows Chrome at `http://127.0.0.1:8765`. |
| Tools and tasks | Linux inside WSL2; this is not a connection to the Windows desktop. |

Installing only the Windows desktop app does not provide the sockets and files this
adapter expects. Selecting WSL as the agent environment in that app also does not, by
itself, establish compatibility with Lipa's bridge.

## 1. Set up WSL2 and WSLg

You need Windows 11 or Windows 10 build 19044+, with virtualization support. See
[Microsoft's Linux GUI application requirements](https://learn.microsoft.com/en-us/windows/wsl/tutorials/gui-apps).

In an **administrator PowerShell**, run:

```powershell
wsl --install -d Ubuntu-24.04
```

Restart if requested, open Ubuntu and create your Linux user. Then check from PowerShell:

```powershell
wsl --update
wsl --list --verbose
```

Ubuntu must show version **2**. To convert an existing WSL1 installation, use
`wsl --set-version Ubuntu-24.04 2`, adjusting the distribution name to match the list.
See [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install).

## 2. Install the Linux desktop app and CLI inside Ubuntu

Run these commands in **Ubuntu**, not PowerShell:

```bash
sudo apt update
sudo apt install git python3 python3-venv ca-certificates
uname -m
```

Download the `.deb` for your architecture from the
[official Linux installation page](https://learn.chatgpt.com/docs/linux/linux-app)
and save it in Ubuntu's filesystem. For x64, from the package's directory:

```bash
sudo apt install ./chatgpt_amd64.deb
chatgpt
```

For ARM64, use `chatgpt_arm64.deb`. Adjust the filename to the downloaded package.
The app must open a visible Linux window through WSLg. Sign in with ChatGPT and leave
it open. If the GUI does not launch, resolve that issue before proceeding.

You also need the Linux `codex` executable in Ubuntu's `PATH`. Follow the
[official CLI installation instructions](https://learn.chatgpt.com/docs/cli), then check:

```bash
command -v codex
codex --version
codex login
```

The command should not resolve to a Windows installation under `/mnt/c/...` or to
`codex.exe`. Sign in inside Ubuntu; signing in only to the Windows app is insufficient.

## 3. Install and run Lipa

In the same Ubuntu environment:

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/StrajnarFilip/lipa.git
cd lipa
./setup.sh
./run.sh
```

Keep the project and `.venv` in the Linux home directory, as shown above. The default
`/usr/lib/chatgpt/resources`, `~/.codex/ipc/ipc.sock` and `/tmp/codex-app-tools/` paths
must belong to the Linux desktop app in this distribution. For another installation
location, use the [README configuration options](../README.en.md#configuration).

## 4. Open the interface in Windows

In **Windows Chrome**, open [http://127.0.0.1:8765](http://127.0.0.1:8765), select
**Začni pogovor** and allow microphone access. Windows can normally reach WSL-hosted
web applications through localhost; see
[Microsoft's WSL networking documentation](https://learn.microsoft.com/en-us/windows/wsl/networking).

Chrome handles the Windows microphone, so you do not need to forward it to Linux.
Handle desktop permissions in the **Linux app's WSLg window**. The **Odpri nalogo v Codex**
link may invoke Windows' `codex://` handler; if it opens the wrong app or fails, select
**Lipa desktop worker** manually in the Linux desktop app.

For later runs, open Ubuntu and the Linux desktop app, then in another Ubuntu terminal:

```bash
cd ~/projects/lipa
./run.sh
```

## Checks and troubleshooting

Try a typed question first, then voice. Check that Lipa shows the desktop connection
and confirms the actual Sol `xhigh` settings for an executed task.

| Symptom | What to check |
| --- | --- |
| Linux desktop app does not open | WSL2, WSL updates and graphics support using Microsoft's WSLg guide. |
| Desktop resources or sockets not found | The Linux app must run in the same distribution and under the same user as Lipa. The Windows installation does not suffice. |
| Resources point to `C:\...` or CLI to `/mnt/c/...` | Check that both desktop and CLI installations are Linux versions. |
| `127.0.0.1:8765` unavailable | Keep `./run.sh` running, check port conflicts, and review WSL, VPN and local firewall settings. |
| Page works but microphone does not | Allow the microphone in Chrome and Windows privacy settings. |
| Private protocol mismatch or voice failure | Compatibility is not established. Record the CLI version and error without authentication tokens. |

To check server reachability from Ubuntu:

```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/api/status').status)"
```

HTTP `200` confirms only server reachability, not working voice or desktop tools.

## Alternative: Windows browser with a Linux host

If Lipa already works on a separate Linux computer, leave Lipa and the desktop app
running there. With the OpenSSH client installed, create a tunnel from PowerShell,
replacing the username and hostname:

```powershell
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 user@linux-host
```

Open `http://127.0.0.1:8765` in Windows Chrome. The microphone and playback are in
Windows; tasks and permissions remain on Linux. You need access to that computer's
graphical desktop for approvals. A `codex://` link in Windows does not automatically
open the remote computer's chat.

Lipa can keep listening on `127.0.0.1`; no public listener is needed. This option
does not require WSL on the Windows computer.
