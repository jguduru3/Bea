# Bea: desktop companion and AI agent

Bea is a fluffy blue character who lives on your desktop, above every other window. She follows your cursor, reacts to everything you do, and works as your assistant:

- **Brain: Claude.** Chat with her, or give her a goal and she plans it, uses her tools and reports back. OpenAI is an optional backup for plain chat.
- **Gets things done** (like Meta Muse): web research with sources, her own browser for websites, Microsoft 365 and Google mail and calendars, your company's APIs, memory, goals, scheduled background tasks and reminders, and documents.
- **Asks before acting.** Sending, booking, buying, posting or changing data waits for your approval; everything she does is in her activity history.
- **Voice: Sarvam AI.** She listens and speaks in English and 10 Indian languages. Say **"Bea"** or **"Hey Bea"** to start talking (the wake word is detected offline).
- **Windows and macOS.**

```
Bea_avatar/
├── desktop/   Electron app: Bea, chat panel, Bea Hub, settings, voice, wake word
├── backend/   FastAPI server: keys, Claude/OpenAI/Sarvam, the agent, history, schedules
├── windows/   one-command installer for Windows
├── mac/       one-command installer for macOS
└── docs/DEVELOPER_GUIDE.md   how everything works and how to change it
```

## Install

**Installers (recommended).** `Bea-Setup-<version>.exe` for Windows and `Bea-<arch>.dmg` for macOS are all-in-one: Bea's server, her browser and the wake-word model are inside, so there's nothing else to install. Run the installer, open Bea, and add your Claude and Sarvam keys in the Settings window that opens. The installers are built by GitHub Actions (Actions → **Build installers**); see [Building and releasing](docs/DEVELOPER_GUIDE.md#12-building-and-releasing). On a Mac, the first time, right-click Bea.app → Open (the builds are not notarised).

**From source with one command** (sets up a separate backend service). You need **Node.js 20+** and **Python 3.10+** (on macOS also `xcode-select --install`).

**Windows 10/11**

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
```

**macOS 11+** (Apple silicon or Intel)

```bash
./mac/install.sh
```

The installer sets up the backend (started at every sign-in, local only on `127.0.0.1:8080`), installs Bea's browser, builds and installs the app with the wake-word model, asks for your keys, and connects everything. You can also add or change keys later in the app: **Settings → API keys** (Claude, OpenAI, Sarvam). Uninstall with `windows\uninstall.ps1` or `mac/uninstall.sh`.

To run from source instead, see [Running it](docs/DEVELOPER_GUIDE.md#3-running-it-windows-and-macos).

## Using Bea

| Do this | What happens |
|---|---|
| Double-click Bea, or Ctrl/⌘ + Shift + Space | Chat panel |
| Say "Hey Bea" (wake word on) or press the mic | She listens; replies are spoken |
| Ask a question | She answers, with her face and body reacting |
| Give a task: "Research our top 3 competitors and write a brief" | She shows her plan and each step live, then answers; the brief opens in Bea Hub |
| "What in my inbox needs a reply today?" | Uses your connected Outlook or Gmail |
| "Email Priya the summary" | Drafts it, then shows an approval card. Nothing is sent until you press Approve |
| "Every weekday at 9:30, check project reports and tell me what's stale" | A background task; results arrive as notifications |
| ⚡ in the chat header | Agent mode on/off (off = quick chat only) |
| ▦ in the chat header, or tray → Bea Hub | Activity, Approvals, Goals, Schedules, Memory, Documents, Connections |
| Right-click Bea | Moves, focus timer, emotions, nap, settings |

**Connecting apps:** Bea Hub → Connections shows the exact setup steps for Microsoft 365 and Google, and lets you add any REST API (for example the Better Analytics project platform) with a token and a short description of its endpoints.

## Developers

Read **[docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)**. It covers:
- **How it works:** the architecture, the agent loop, approvals, the wake word and the security model.
- **The code:** a walkthrough of every file.
- **Changing it:** recipes for adding a tool, an app connection, an emotion, a setting or a Hub tab.
- **Shipping it:** testing, building and the full configuration reference.

```bash
cd backend && .venv/Scripts/python -m pytest -q     # backend tests (macOS: .venv/bin/python)
cd desktop && npm test && npm run check             # desktop tests and syntax check
```
