<div align="center">

# ⚙️ JUDO — AI Assistant
### RITESH LIII · The Ultimate Cross-Platform Personal AI Assistant

[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-informational)](#-requirements)
[![Engine](https://img.shields.io/badge/Engine-Gemini%203.1%20Flash%20Live-8E44AD)](#-whats-new-in-ritesh-liii)
[![License](https://img.shields.io/badge/License-MIT-lightgrey)](#%EF%B8%8F-license)

**A real-time voice AI that can hear, see, understand, and control your computer — on any OS.**
Built on the Gemini Live API for native audio streaming, delivering zero subscriptions and total digital autonomy.

</div>

---

## ✨ Overview

**RITESH LIII is the hands-free & scalable release.** Say **"Hey Judo"** and it wakes; stay quiet and it slips back to sleep on its own — while asleep, your microphone never leaves the machine, so an off-hand *"I'll be right there"* to someone in the room no longer sets it off. Under the hood it now runs on the faster **Gemini 3.1 Flash Live** engine, and the moment you ask for something that takes a beat — analysing a file, searching the web — it answers instantly *("On it — going through that now…")* so you never wonder whether it heard you.

It's also built to grow: every skill — bundled or drop-in — now **describes itself in its own file**, so adding a tool is a one-file operation and the core stays lean.

The newest additions make JUDO feel less like a tool and more like someone there with you: it has **a face and a body** — a 3D anime character who breathes, blinks, lip-syncs to its voice and gestures while it talks — it **starts conversations on its own** (good morning, how did the interview go?) and **learns from your answers**, and it ships **its own browser, JUDO Browser**, so every website it opens for you lives in one place it fully controls.

It's not just an assistant — it's an extension of your digital life.

---

## 🚀 Capabilities

### Core Features
| Feature | Description |
|---|---|
| 🎙️ Wake Word | Local **"Hey Judo"** detection — sleeps until called, auto-sleeps after 2 min of silence, and never streams audio while asleep. Opt-in, one-click download, toggle & manual sleep/wake from the UI |
| ⚡ Instant Acknowledgment | Speaks a short, context-aware reply in **your language** the instant a longer task starts — no more silent waiting |
| 🚀 Faster Live Engine | Runs on **Gemini 3.1 Flash Live** — roughly 2× faster time-to-first-word than the previous model |
| 🧩 Self-Describing Skills | Actions and plugins share one shape (`TOOL` / `PLUGIN` dict + `run()`), auto-discovered at launch — adding or moving a skill is a single file, no core edits |
| 🧠 Recallable Memory | No size limit and nothing silently forgotten — the prompt carries what fits, the rest is looked up on demand from a local search |
| 👁️ Memory Panel | See every fact JUDO has stored about you, when it learned it, and delete any of it in one click |
| ↩️ Undo | Take back what the assistant did — files it moved, renamed, created or wrote, and settings it changed |
| ⚠️ Real Confirmation | Shutdown, restart and WiFi wait for a button **you** press — the model cannot confirm its own irreversible actions |
| 🎧 Audio Device Picker | Choose the microphone and speakers by name, filtered to the short list your OS shows — and measured, so every entry actually works |
| 🔗 Session Continuity | A dropped connection, a voice change or a device change no longer wipes the conversation |
| 🧩 Plugin System | Drop a single `.py` file into `plugins/` — JUDO learns a new skill on next launch |
| 🎙️ Real-time Voice | Ultra-low latency conversation in any language via Gemini Live API |
| 🎨 Live Theming | Recolour the entire HUD from a hue wheel or hex — applied instantly across every panel |
| 〰️ Reactive HUD | Waveform and reactor core pulse to real audio — your mic while listening, JUDO while speaking |
| 🎙️ Voice Picker | Choose from 5 native Gemini voices and switch live from the UI — no restart |
| ♾️ Unlimited Sessions | Sliding-window context compression — one conversation can last for hours |
| 🖥️ System Control | Launch apps, adjust volume/brightness, WiFi, shortcuts, power — all by voice |
| 🧩 Autonomous Tasks | High-level planning for complex multi-step goals via agent mode |
| 👁️ Visual Awareness | Real-time screen capture and webcam vision piped into your main Gemini session |
| 🧠 Persistent Memory | Deeply remembers projects, preferences, and personal context across sessions |
| ⌨️ Hybrid Input | Seamlessly switch between keyboard typing and voice commands |
| 🗓️ Session Memory | Summarises each conversation and mentions it naturally next morning — consumed after use, never repeats |
| 👁️‍🗨️ Background Monitoring | User-configured topic watching — checks for new headlines once a day and alerts naturally |
| 📊 Hardware Monitoring | Continuous CPU, RAM, GPU and temperature telemetry with localized voice alerts |
| 🌤️ Weather Report | Live weather data for your city, personalized from memory |
| 🗺️ Dynamic Content Panel | Scrollable display layer beneath the HUD that renders web results, news, and search data |
| 🔍 Multi-Mode Web Search | `news` / `research` / `price` / `compare` / `search` — Gemini Grounded first, DDG fallback |
| ⏰ Smart Reminders | OS-native scheduled notifications (Windows Task Scheduler / macOS LaunchAgent / Linux systemd) |
| ✈️ Flight Finder | Live flight price and availability lookup |
| 🎮 Game Updater | Checks and triggers game updates on Steam and Epic Games on demand |
| 📂 File Processor | Read, summarize, and answer questions about local files |
| 💻 Code Helper | Inline code review, debugging, and generation |
| 🌐 JUDO Browser | JUDO's own Chrome-like browser (Chromium engine) — every site, search and link JUDO opens goes here, never Chrome or Edge. Tabs, smart address bar, bookmarks, history, downloads, encrypted passwords, incognito, DevTools, sandboxed pages |
| 🎨 Customize JUDO | Google-style New Tab page with an animated 3D JUDO logo, search suggestions, editable shortcuts, light / dark / device mode, colour themes that tint the whole browser, and backgrounds (or your own photo) |
| 👤 JUDO Accounts | Several people or profiles in one browser — each with its own logins, cookies and passwords — plus a "Manage your JUDO Account" page (personal info, data & privacy, security, accounts) |
| 🌐 Browser Control | Drives JUDO Browser by voice — opens URLs, searches, clicks, types, presses keys and reads pages; "Chrome kholo" / "open in Edge" also mean JUDO Browser |
| 📬 JUDO Mail | A Gmail client window over IMAP/SMTP — folders, search, read, attachments, compose / reply / forward; links open in JUDO Browser |
| 📨 Send Message | Compose and send messages through WhatsApp, Telegram, and more |
| 🎬 YouTube Control | Search, play, and control YouTube playback by voice |
| 🖱️ Desktop Control | Taskbar, window management, and desktop-level operations |
| 🧑‍💻 Silent Language Memory | Detects spoken language on first use — all future sessions adapt automatically |
| 📱 Remote Dashboard | Control the assistant from your phone via QR code pairing — the phone's mic streams live into the same session running on your computer, so a command spoken on the phone executes on the computer |
| ⚡ Auto-Start on Boot | Registers with the OS startup system (registry / LaunchAgent / .desktop) — and repairs the entry itself if the project folder moves |
| 🔒 One JUDO at a time | Starting JUDO while it is already running shows a message instead of a second copy fighting over the mic and the dashboard port |
| 📋 Clipboard Intelligence | Copy any text → floating panel with Translate / Summarise / Explain / Fix |
| 🪪 Assistant Customization | Change the assistant name, your name, voice, and colour from the UI — takes effect immediately |
| 🎛️ Media Control | Play/pause, skip, previous and stop for whatever is currently playing — Spotify, YouTube, or any player |
| 🔁 Unit Converter | Converts length, weight, temperature and currency (live exchange rate) on request |
| 📅 Calendar / Agenda | Local event calendar — add, list today's/tomorrow's/this week's agenda, remove — no external account needed |
| 🧠 Quiz Mode | Voice-driven quiz on any topic — generates questions locally, checks spoken answers, keeps score |
| 📧 Email (Gmail) | Sends email through JUDO Browser and your real, already-logged-in Google account — no password ever stored |
| 🔑 Multi-Key Failover | Configure more than one Gemini API key — on a quota/rate-limit hit, JUDO rotates to the next one automatically instead of going down |
| 🗣️ Gender-Aware Grammar | Self-reference and address use grammatically correct gender agreement (languages that require it), matched to your and JUDO's configured gender |

---

## 🤖 Task Agent — tell it the goal, it does the steps

Say *"open my AI Assistant project, find why login fails, fix it, run the tests, commit and push"*.
JUDO hands this to its task agent. A supervisor plans the work, picks only the agents it needs
(Computer, Coding, Testing, Git, GitHub, Research, File/Document, Browser, Messaging, Email,
Calendar, Deployment) and runs a plan → act → observe → verify loop, while you keep talking.

* **Remembers the task:** "fix it", "push it", "this PDF" refer to the current project, issue or attached file.
* **Keeps you posted:** short spoken milestones, plus a live **AGENT** box on the HUD.
* **Asks only when it must:** pushes, messages, emails, invites, deploys and deletions wait for your
  HUD button. The check is enforced below the model.
* **Never fakes a result:** the report's *Verified* list comes from real tool results — tests passed,
  commit sha, push checked on the remote, message id from the provider.
* **Claude Code:** it can prepare a precise, repo-grounded Claude Code prompt with **COPY PROMPT**,
  or run the `claude` CLI locally when you enable it.
* **Text mode:** the same agent runs without voice: `python -m agent "…"`.

Docs: [architecture](docs/ARCHITECTURE.md) · [agents & tools](docs/AGENTS.md) ·
[integrations & setup](docs/INTEGRATIONS.md). Optional integrations (Telegram/Slack/Discord/WhatsApp
Cloud, Gmail/Outlook, Google Calendar, Vercel/Render, MCP servers) are configured in
`config/api_keys.json` and `config/agent.json`. Anything not connected says so instead of pretending.

---

## 🌟 Latest Additions

Everything below is built on top of RITESH LIII.

### 🌐 JUDO Browser — JUDO's own browser

```
python -m judo_browser              # or just say "YouTube kholo" / "browser kholo"
python -m judo_browser github.com   # open a site (hands it to the running browser if one is open)
```

A Chrome-like browser on the Chromium engine (Qt WebEngine), made for JUDO. **Every website, search and link JUDO opens goes here — it never opens Chrome or Edge**, even when you say "open in Chrome". If JUDO Browser can't do something, JUDO says so instead of silently switching browsers. (To go back to driving Edge, set `"default_browser": "edge"` in `config/api_keys.json`.)

**Voice control.** JUDO drives it over a private local connection: go to a site (switching to an already-open tab instead of opening a duplicate), search, open tabs, click, type, fill forms, press keys, scroll, read the page, take screenshots. It starts by itself when closed. JUDO Mail, WhatsApp, YouTube and weather links open here too.

**Everyday browser features.** Tabs (pin, mute, duplicate, reopen closed), address bar with search suggestions, bookmarks and bookmarks bar, history, downloads, find in page, per-site zoom, print, save page, view source, DevTools, site permissions, session restore, incognito windows, Chrome keyboard shortcuts. Saved passwords are encrypted with Windows DPAPI — only your Windows account on this PC can read them.

**New Tab page.** Laid out like Google's home page, with JUDO's own identity:
- the **JUDO logo in 3D** — letters drop in one by one, float, catch a light sheen, lean toward your mouse, and spin when clicked — in JUDO's own palette, **JUDO Spectrum** (violet `#7B5CFF` · rose `#FF4D8D` · amber `#FFA62B` · aqua `#12C9B4`), also used for the app icon
- a search box with live suggestions (from your history and Google, Hindi included)
- **shortcuts** with real site icons — add, edit or remove them (right-click or ⋮), with Undo; up to 10
- an apps launcher, and links to history, downloads, bookmarks and settings

**Customize JUDO** (button on the New Tab page, or ⋮ → Customize JUDO): **Light / Dark / Device** mode (Device follows Windows live), 12 colour themes plus a custom colour that tint the tabs, toolbar and frame, 12 backgrounds or a photo from your PC, and shortcut options. Light / Dark / Device is also in ⋮ → Appearance and in Settings.

**JUDO Accounts.** Click the avatar on the New Tab page for a Google-style account menu: switch accounts, **Add another account**, **Remove an account**, **Sign out of all accounts**, and **Manage your JUDO Account**. Each account is a separate browser profile with its **own logins, cookies, cache and saved passwords** (history, bookmarks and settings are shared), opening in its own window. The account page (`judo://account`) has Home, Personal info (name, email, colour, picture), Data & privacy (history, clear browsing data, download your data as JSON), Security (sandbox status, password manager, sign-out) and Accounts. JUDO Accounts live only on this PC.

**About JUDO Browser** (`judo://about`): version, *Made by Ritesh*, what JUDO is, features and copyable technical details.

**Security.** Pages run in the **Chromium sandbox**. When Python is installed inside the user profile, Windows doesn't let the sandboxed page process read Qt's files and every page would stay blank; the browser grants that folder read access once at start-up (what Chrome's own installer does) and only falls back to no sandbox if it can't. JUDO's own pages (New Tab, Account, About) talk to the browser with a per-run secret token, so no website can trigger their actions.

### 📬 JUDO Mail

```
python -m judo_mail
```

A Gmail client window over IMAP/SMTP: folders, a paged message list, Gmail search syntax, reading mail (its scripts turned off, links opening in JUDO Browser), attachments, compose / reply / reply-all / forward, star, mark unread and delete. Needs `gmail_address` and `gmail_app_password` (a Google App Password, not your account password) in `config/api_keys.json`.

### 🧍‍♀️ A face and a body — the 3D avatar

Put a **`.vrm`** model in **`avatar/models/`** and JUDO shows that character, **full body**, in the middle of its window. Every motion is procedural, so no animation files are needed and any VRM works:

| JUDO is… | The character… |
|---|---|
| starting | waves hello |
| listening | breathes, shifts weight from foot to foot, smiles, looks around — and at your mouse pointer |
| speaking | lip-syncs to the real loudness of JUDO's voice (aa / ih / ou / ee / oh), nods, gestures with an open hand |
| thinking | rests a hand on the chin, tilts the head, looks up |
| sleeping | head down, eyes closed |

Hair and clothes swing with spring-bone physics, and the eyes blink at random. **Getting a model:** download one from [VRoid Hub](https://hub.vroid.com) whose terms allow avatar use, or make your own in the free VRoid Studio and export it as VRM. The newest file in the folder is used. `avatar/models/README.md` has the steps. `.vrm` files are git-ignored because each has its own licence. pixiv's official sample model works too: its VRM licence allows avatar use by everyone, commercial use and redistribution, with no credit required.

The viewer (`avatar/viewer.html` + `viewer.js`, three.js + three-vrm, both MIT) is served to a transparent web view on a private local port, with the libraries kept in `avatar/vendor/`, **so it works offline**. With no model, the HUD shows an animated **2D anime face** (`anime_face.py`) that blinks, lip-syncs and changes expression by state.

### 💬 A JUDO that talks first — and learns from it

- **Startup hello.** JUDO opens with the right greeting for the time of day and **one question**: how you are, what's on today, or how last time's thing went. Today's headlines appear on screen quietly instead of being read over your answer.
- **Four greetings a day.** **Good morning** (from 5 am), **Good afternoon** (12), **Good evening** (5 pm) and **Good night** (9 pm). Each comes once, as soon as that part of the day begins and you aren't mid-sentence, and is remembered across restarts (`memory/greetings.json`).
- **Check-ins every 10 minutes, 24 hours a day.** After 10 quiet minutes it says something, and again every 10 minutes the quiet lasts. Each check-in is at most two short sentences in your language, ends with one question, and rotates between how you are, your work, a follow-up on something earlier, getting to know you, and something interesting. Late at night it asks about rest instead of work.
- **Learning.** Once your answer settles, the facts it reveals (plans, likes, people, habits) are saved to long-term memory, so the next question builds on them. Every exchange (question, answer, answered or not, what was learned) is appended to **`memory/human_conversations.jsonl`**, JUDO's own conversation training data. Its last questions are fed back in so it doesn't repeat itself. Both files are git-ignored.

### 🗣️ JUDO's own voice — `voice_training/`

Notebooks that turn one reference recording into a voice model JUDO can run on a plain CPU, offline:

1. **IndicF5** speaks 1,932 Hindi sentences in the reference voice → `judo_voice_dataset.zip`
2. **Piper** is fine-tuned on that dataset → `judo_voice_model.zip` (`judo_voice.onnx` + `.json`)

- **Google Colab (2 cells):** upload `judo_voice_colab_all.zip`, then run `run_all.sh`. Step-by-step guide: `voice_training/colab_commands.md`.
- **Kaggle:** use the two notebooks.

Rebuild all of them with `python voice_training/build_notebooks.py`. Needs a Hugging Face token (`HF_TOKEN`) and access to the gated IndicF5 model.

### 🌱 Seed generator — 1,00,000 real-sounding commands

`core/seed_generator.py` grows the routing exam beyond the 20,000 template commands. One model family writes new commands for a tool in a given speaking style (speech-recognition typos, rambling, indirect needs, Hinglish, Devanagari…), and a model from the other family must route each one blind to the same tool and details before it is kept (`memory/seed_tasks.jsonl`).

```
python -m core.seed_generator --status     # progress, thinnest labels
python -m core.seed_generator              # run (resumable); run_seed_generator.ps1 starts it at logon
```

It needs internet. Without it, it waits instead of stalling and carries on when the connection returns. Every Gemini call now has a 120-second limit, so one unanswered request can no longer freeze it.

### 🩹 Reliability

- **One JUDO at a time.** A second launch shows a message instead of two copies sharing the mic.
- **Dashboard port busy.** If port 8000 is taken, only the phone dashboard is turned off; the voice session keeps running.
- **Auto-start repairs itself.** If the registry entry points to an old folder, it is rewritten to the current one (it used to fail silently at logon).
- **No more hung text-model calls** (120-second limit).
- **Taskbar.** JUDO Browser shows its own icon instead of Python's.
- **HUD clock.** Now in 12-hour time (`12:10:44 AM`).

---

## 🆕 What's New in RITESH LIII

RITESH LIII is about making JUDO **hands-free, faster, and easy to extend** — all universal: no hardcoded language, no bundled asset files, works the same on Windows, macOS and Linux.

### 🎙️ Wake Word — "Hey Judo"
JUDO can now sit quietly until you call it. Turn on **⚙ → WAKE WORD** (a one-click, opt-in download of a tiny local model) and it goes to sleep: the microphone is processed **only on your machine** by a local detector, and nothing is sent to the cloud until it hears **"Hey Judo."** Once awake it listens normally, then **auto-sleeps after 2 minutes** of silence. You can also **sleep/wake it by clicking** in the settings. Because it's a *local* gate, background chatter — *"I'm coming!"* to someone at home — never wakes it. It costs **zero** when off (the model isn't even loaded), and the detection runs in its own thread, so nothing else in the app slows down.

> **Note:** the underlying detector (`openwakeword`) only ships pretrained models for a fixed set of phrases (alexa, hey_mycroft, hey_jarvis, …) — there is no pretrained "Hey Judo" model yet. Until a custom one is trained (openWakeWord's own training pipeline, run outside the app) and dropped in, ⚙ → WAKE WORD will report the wake model as unavailable rather than silently listening for the wrong word.

### ⚡ Instant Acknowledgment
No more silent gaps. When you ask for something that takes a moment — reading an uploaded file, a web/research search, building code — JUDO **immediately** says one short, natural sentence *in your language* (*"Right away — going through that file now."*) and *then* runs the tool. Instant actions (opening an app, volume) stay snappy with no chatter.

### 🚀 Faster Live Engine — Gemini 3.1 Flash Live
The live session moved to **`gemini-3.1-flash-live-preview`**, cutting the time-to-first-word roughly in half while keeping tools, all five voices, transcription, session resumption and sliding-window compression intact.

### 🧩 Self-Describing Skills — a Scalable Core
Every bundled **action** now carries its own `TOOL` declaration in its own file (exactly like a drop-in **plugin's** `PLUGIN` dict), and the core auto-discovers them at launch. `main.py` no longer holds a giant list of tool definitions and dispatch branches — it shrank by hundreds of lines. Adding a new built-in skill, or promoting an `actions/*.py` file into a shareable plugin, is now just… moving a file.

### 🔑 Multi-key failover
A single Gemini API key means the assistant goes down the moment its free-tier quota is exhausted. `config/api_keys.json` now accepts a `gemini_api_keys` list instead of (or alongside) the single `gemini_api_key` field; on a quota/rate-limit (429) error the live connection loop rotates to the next configured key automatically and keeps going.

### 🗣️ Gender-aware grammar
Languages with grammatical gender (e.g. self-referring verb/adjective agreement) need to know whose gender is being expressed. JUDO now reads a configured gender for both the user and the assistant's own voice, and uses that to keep self-reference and address grammatically correct instead of defaulting to one gender for everyone.

### 🌐 Real-browser automation
`browser_control` used to open simple "go to this site" requests in the real, already-open browser but fall back to a second, separate, signed-out automation profile the moment an interactive action (click/type) was needed — so a flow like "open ChatGPT and ask it X" could end up typing into a browser window that was never logged in anywhere. It now drives the user's actual already-open Chrome/Edge/Brave/Vivaldi/Opera via the Chrome DevTools Protocol for every action, including plain navigation — one browser, real profile, no second window. JUDO **never closes, kills or restarts your browser**: websites open as new tabs in the window you already have open. Clicking, typing and reading inside a page work when the browser was started with remote debugging; otherwise JUDO says so instead of restarting it (Chrome 136+ also ignores the debug flag on your normal profile, so a restart could never have worked there anyway).

> **Since then:** JUDO now uses **its own browser** — see [JUDO Browser](#-judo-browser--judos-own-browser). The CDP path above is only used when `"default_browser": "edge"` is set.

> Built on the RITESH LI/LII foundation: the **🧩 Plugin System**, **♾️ Unlimited Sessions**, **🎨 Live Theming**, **〰️ Reactive HUD** and **🎙️ Voice Picker** are all still here.

---

## 🔄 The Foundation Update — in every release from LII

These four landed across **RITESH LII, LIII, LIV and LV at the same time**, after each of those releases had already shipped. They are not what any one of those versions originally introduced; they are the floor all of them now stand on, so moving up a release never costs you something the one below it had.

No new dependencies. No bundled asset files. No hardcoded language, and nothing that assumes one operating system.

### 🧠 A memory that actually remembers

The store was capped at **2,200 characters — the whole memory, not per entry** — because all of it was pasted into the system prompt on every connect, so growing the memory grew every request. When it filled, the oldest entries were deleted and one line was printed to a console nobody reads. An assistant advertised as remembering "projects, preferences and personal context" was in practice a two-page notepad that quietly forgot your sister's name after a few weeks.

Storage and prompt budget are now separate problems:

* **Nothing is deleted.** The cap is a runaway guard normal use never approaches, and if it is ever hit it says so in the activity log instead of on stdout.
* **The prompt carries a core, not a dump.** Identity in full, then the most recently updated facts, budgeted — measured at **971 characters on a memory holding 62 stored facts.** That is *smaller* than the old whole-store cap, so sessions now connect with fewer tokens than before.
* **The rest is fetched on demand.** A `recall_memory` tool searches the full store locally — no network, no second model, well under a millisecond.

The part that is easy to get wrong: **a model cannot look something up if it doesn't know the thing exists.** So the prompt also carries an **index of the keys** it had no room for. Without it, "who is Ayşe?" gets "I don't know" while `ayse_sister` sits on disk unread. That index interleaves categories rather than sorting by recency — sorted like the core, a memory with forty preferences pushed the one entry the index existed for off the end.

⚙ → **🧠 MEMORY** shows every stored fact, when it was learned, and a ✕ to forget it. Everything stays in `memory/long_term.json` on your machine.

### ↩️ Undo — it can take back what it did

JUDO moves files, renames them, writes to them and changes your settings. None of that had a way back; if it misheard you, the only remedy was to fix it by hand.

Say **"undo"** — in any language — and it reverses its own last action:

| | |
|---|---|
| **Files** | move · rename · create · copy · write · delete · organize desktop |
| **Settings** | volume · brightness · dark mode |

Three things it deliberately does *not* do:

* **It does not guess.** Settings undo reads the current value *before* changing it. Where a platform won't report that value, nothing is registered — an undo that restores a guess is worse than no undo.
* **It does not hoard.** Undoing a write means keeping the old contents in memory, so files over 1 MB are excluded and it says so rather than holding a 200 MB log for the session.
* **It does not delete your files to undo a copy.** The reverse of a copy is removing the copy; the reverse of "create a folder" is removing it *only while it's still empty*.

`organize_desktop` gets special treatment — one command that moves dozens of files, which made it the least reversible thing the assistant could do. It journals every move and puts all of them back in one go, cleaning up the folders it created if they're still empty.

**Undo costs nothing at runtime.** It appends a closure to a list; nothing in it runs unless you ask.

### ⚠️ A confirmation the model can't forge

The old gate read like this:

```python
if action in _DANGEROUS_ACTIONS:            # {"restart", "shutdown"}
    confirmed = str(params.get("confirmed", "")).lower()
```

`confirmed` is a **tool parameter, which means the model fills it in.** Nothing stopped it sending `confirmed=yes` on the first call and nothing checked that a human was ever involved. It was a convention, not a gate. And its coverage was two actions — so `toggle_wifi`, which cuts the assistant's own connection to the Live API and therefore *cannot be asked to undo itself*, went through with no gate at all.

The token is now issued by the interface. Shutdown, restart and WiFi put a banner on the HUD and **return immediately**; the action runs only if you press CONFIRM. Nothing blocks — JUDO keeps talking while the banner is up — so this is **cheaper than the old gate**, which burned two tool round trips on every power command.

> The split between the two mechanisms is about reversibility, not about how alarming a word sounds. Anything undoable is done at once; only the genuinely irreversible asks. An assistant that checks with you before turning the volume down is one you stop talking to.

### 🎧 It finally asks which microphone

Both audio streams opened with no device argument at all, so they always took whatever the OS called "default" — and on Windows that *moves on its own* the moment you plug a headset in. "JUDO can't hear me" almost always meant "JUDO is listening to the webcam".

⚙ → **🎧 AUDIO DEVICES** lets you pick the microphone and the speakers by name. Two things matter more than the dropdown:

**The list is short.** `query_devices()` returns one entry per *device × host API*, not per device — measured on an ordinary Windows machine, **41 entries for what the sound settings show as 4 microphones and 4 speakers.** The same microphone appears four times, under MME, DirectSound, WASAPI and WDM-KS, with nothing to say which is which. That is not a choice, it's a quiz. The picker takes one host API per direction, drops the "Sound Mapper" and "Primary Sound Driver" pseudo-devices that just mean "default", and deduplicates. **41 → 8.**

**Every entry has been measured, not assumed.** The obvious approach is to pick the host API with the nicest names — WASAPI on Windows, which in shared mode **doesn't resample**, so with 16 kHz in and 24 kHz out against 48 kHz hardware every open failed. Adding a rate check and moving to DirectSound passes that test on both sides, and PortAudio's DirectSound **output is a silent sink**: the stream opens, every write returns success in ~0 ms, and not one sample reaches the speakers.

| | write(2.0 s) took | |
|---|---|---|
| MME | **2.02 s** | consumed in real time |
| DirectSound | **0.00 s** | swallowed instantly |

No capability flag reports that. So the app measures it — once per host API per direction, on a background thread at startup, using silence. Two consequences worth stating plainly:

* **Each direction picks its own host API.** On Windows this lands on DirectSound for the microphone and MME for the speakers — a split no amount of reasoning would have produced.
* **The probe runs in the mode the app actually ships.** DirectSound input passes a callback stream and fails a blocking read; probing the wrong mode rejected a microphone that works perfectly.

Your choice is stored **by name, not by index** — indices shift whenever something is plugged in. If the saved device is gone, it falls back to the system default and says so in the log rather than failing to start.

### 🔗 It stops forgetting the conversation when the connection drops

`session_resumption` was switched on in the config and the handle the server sent back was **never read** — so every reconnect started an empty session. A dropped packet, or simply changing the voice, wiped the conversation. "Unlimited sessions" leaked through exactly this hole.

The handle is captured and replayed now. A network blip, or switching your microphone, keeps the conversation intact.

It is held in memory only, deliberately: writing it to disk would make a fresh launch continue yesterday's chat, which sounds appealing but breaks the session-summary flow — a conversation that never ends never produces a summary, and the "yesterday we talked about…" line in the morning briefing silently disappears. Changing the **voice** also starts clean on purpose, since resuming restores the server's session state and would likely bring the old voice back with it.

### 🩹 Fixes that came with it

* **The assistant could die on a log line.** Status lines carry emoji and arrows (`📤 file_controller → Moved: a.txt → Documents/`). On a non-UTF-8 console — cp1254 on a Turkish Windows, cp1251 on a Russian one, cp932 on a Japanese one — printing one raises `UnicodeEncodeError`, and because that print sits *after* the tool's own `try/except`, it escaped into the receive loop and took the session down.
* **Every computer command paid for two model round trips.** `computer_settings` made an *entire second Gemini call, inside the tool*, purely to translate the request into one of its own action names — because the declaration only said "The action to perform", so the model rarely filled it in. When that second call failed, the fallback was `description.lower().replace(" ", "_")`, which turns the Turkish for "turn it down" into `sesi_kis` and straight into "Unknown action". The declaration now names all 56 actions and the rest is spelling tolerance handled locally by `difflib` in microseconds. When nothing matches it suggests real action names instead of dead-ending.
* **Volume up/down could silently do almost nothing.** `media_control` and `computer_settings` both used to claim `volume_up`/`volume_down`/`mute`, with different step sizes and no shared undo state — the model picked between them non-deterministically, so a "turn it up" sometimes landed as a single, barely-perceptible key press. `media_control` now only handles actual playback transport (play/pause/skip/stop); `computer_settings` is the one place system volume lives.
* An unresolvable saved audio device, or one the driver refuses to open, falls back to the system default and says so — on both the microphone and the speakers.
* A rejected session-resumption handle is dropped after one attempt, so an expired handle can never be replayed on every retry and prevent the reconnect it exists to protect.

---

## 🎯 Routing Exam — 20,000 commands JUDO trains on

JUDO understands you by picking the right tool for what you said. To make that reliable it sits a fixed exam of **20,000 spoken commands** (English, Hinglish and Hindi) with a known right answer each, covering every tool — `core/training_corpus.py`.

- **Every wrong pick is a lesson.** `core/routing_trainer.py` records each confusion ("wanted `media_control`, picked `computer_settings`") with example phrasings. The most frequent ones go into the live voice prompt as `[ROUTING LESSONS]`, so the same phrasing does not fool it twice.
- **Mistakes are re-tested.** Wrong items come back in later batches; once they pass, the lesson fades out by itself.
- **It keeps practising while you're idle.** The idle self-trainer works through the exam 50 commands at a time, alongside its coding drills.
- **Nothing is executed.** The exam only asks "which tool would you pick" — it never opens, sends or deletes anything.
- **Quota-safe.** It uses only the Gemma models (14,400 requests/day) and backs off when rate-limited, so it never eats the quota JUDO's live actions need.

```
python -m core.routing_trainer              # run the whole exam now (resumable)
python -m core.routing_trainer --status     # accuracy, weakest tools, current lessons
python -m core.routing_trainer --reset      # start over
```

Turn background practice off with `"self_training_enabled": false` in `config/api_keys.json`.

## 🗺️ RITESH Roadmap

Each release is named **RITESH \<roman numeral\>** — the version column below is that numeral.

| Version | Focus |
|---|---|
| **XLIX** | Auto-start · clipboard intelligence · assistant customization |
| **L** | Session memory · background monitoring · proactive 2.0 · instant vision |
| **LI** | Plugin system · affective dialog · proactive audio · unlimited sessions |
| **LII** | Voice picker · live theming · reactive HUD · recallable memory · undo · real confirmation · audio device picker · session continuity |
| **LIII** | Wake word · Gemini 3.1 Flash Live · instant acknowledgment · self-describing action/plugin architecture · multi-key rotation · gender-aware voice agreement · real-browser (CDP) automation |
| *shared* | The last five above also shipped to LIII, LIV and LV at the same time — moving up a release never loses them |
| **LIV+** | Plugin files: email · quiz mode · calendar · home assistant · 3D-printer · media control · unit converter · and more |

---

## ⚡ Quick Start

```bash
git clone https://github.com/RiteshKumar2e/Ai-Assistant.git
cd Ai-Assistant
python setup.py        # installs deps for YOUR OS + the browser automation engine
python main.py         # JUDO itself (only one copy runs at a time)
```

```bash
python -m judo_browser                       # JUDO Browser on its own
python -m judo_mail                          # JUDO Mail (Gmail)
python -m core.seed_generator --status       # seed-generator progress
pytest tests/                                # the test suite
```

Want the 3D character? Drop a `.vrm` model into `avatar/models/` (see *A face and a body — the 3D avatar* above) and restart JUDO.

`setup.py` only ever installs what your operating system needs — the Windows-only libraries are skipped automatically on macOS and Linux (and vice-versa). Prefer to do it by hand? `pip install -r requirements.txt` works too.

> ⚠️ **Installation Note:** If you hit a `ModuleNotFoundError` for an OS-specific package, install it with `pip install <module_name>`. The optional **wake word** engine is *not* installed here — grab it in one click from **⚙ → WAKE WORD** inside the app.

---

## 📋 Requirements

| Requirement | Details |
| --- | --- |
| **OS** | Windows 10/11, macOS, or Linux |
| **Python** | 3.11 or 3.12 |
| **Microphone** | Required for voice interaction (and for the "Hey Judo" wake word) |
| **Speakers** | Required for voice replies |
| **Qt WebEngine** | `PyQt6-WebEngine` (in `requirements.txt`) — JUDO Browser, JUDO Mail and the 3D avatar run on it |
| **API Key** | Free Gemini API key (entered on first launch → `config/api_keys.json`; more than one can be configured for automatic failover) |
| **3D avatar** *(optional)* | Any `.vrm` model in `avatar/models/` — runs on integrated graphics; very high-poly models may slow the HUD |
| **Wake word** *(optional)* | One-click download from ⚙ → WAKE WORD (`openwakeword`, a few MB, fully local) — see the note in [What's New](#-whats-new-in-ritesh-liii) about the "Hey Judo" model |

---

## 🗂️ Project Structure

```
Ai-Assistant/
├── main.py                   # Core loop — Gemini Live session, audio I/O, wake/sleep, greetings & check-ins, tool dispatch
├── ui.py                     # PyQt6 HUD — reactive waveform, log panel, settings drawer, plugin manager, camera feed
├── anime_face.py             # Animated 2D anime face for the HUD (shown when no 3D model is installed)
├── avatar_view.py            # The 3D character inside the HUD — transparent web view fed JUDO's state and voice level
├── avatar/
│   ├── viewer.html / viewer.js  # three.js + three-vrm viewer — procedural breathing, blinking, lip-sync, gestures
│   ├── vendor/               # three.js, GLTFLoader, three-vrm (MIT) — bundled so the avatar works offline
│   └── models/               # Put your .vrm here (git-ignored); README.md explains where to get one
├── judo_browser/
│   ├── app.py                # JUDO Browser — windows, tabs, omnibox, bookmarks, downloads, accounts, sandbox
│   ├── newtab.py             # New Tab page — 3D JUDO logo, search, shortcuts, apps, Customize JUDO panel
│   ├── account_page.py       # "Manage your JUDO Account" (judo://account)
│   ├── about_page.py         # About JUDO Browser (judo://about)
│   ├── accounts.py           # JUDO Accounts — one browser profile and password store per account
│   ├── control.py / client.py # Local voice-control channel between JUDO and the browser
│   ├── theme.py              # Light/dark/device themes, colour tints, JUDO Spectrum palette and app icon
│   ├── passwords.py          # Saved passwords, DPAPI-encrypted, filled per exact site
│   ├── omnibox.py / dialogs.py / app_data.py  # Address bar, settings/history/downloads dialogs, settings storage
├── judo_mail/                # JUDO Mail — Gmail over IMAP/SMTP (mailbox.py) in a PyQt window (app.py)
├── voice_training/           # Notebooks + Colab bundle that train JUDO's own Piper voice from one recording
├── run_seed_generator.ps1    # Starts the seed generator in the background (used at logon)
├── setup.py                  # OS-aware installer (skips wrong-OS dependencies)
├── plugins/
│   ├── _template.py          # Copy this to write a new plugin — one file, drop in, done
│   ├── media_control.py      # Play/pause, skip, previous, stop for whatever is currently playing
│   ├── unit_converter.py     # Length, weight, temperature & live-rate currency conversion
│   ├── calendar_agenda.py    # Local calendar — add/list/remove events, ties into reminder for alerts
│   ├── quiz_mode.py          # Voice quiz — local question generation, scoring, multi-turn state
│   ├── send_email.py         # Sends Gmail via the user's real logged-in browser session, no password stored
│   └── ...                   # Drop-in skills (each self-describes via a PLUGIN dict + run())
├── actions/                  # Bundled skills — each self-describes via a TOOL dict + handler
│   ├── web_search.py         # Gemini + DDG parallel search (news, research, price, compare)
│   ├── screen_processor.py   # Screen & webcam capture for vision
│   ├── background_monitor.py # User-configured topic watching — daily DDG check, no crypto
│   ├── proactive.py          # Proactive 3.0 — day-part greetings, 10-minute check-ins, learning from answers
│   ├── reminder.py           # OS-native scheduled notifications
│   ├── system_monitor.py     # CPU / RAM / GPU / temperature telemetry
│   ├── computer_settings.py  # Volume, brightness, WiFi, power (per-OS) — owns system volume/mute
│   ├── computer_control.py   # Keyboard shortcuts, mouse, window management
│   ├── open_app.py           # Application launcher (per-OS name map)
│   ├── open_folder.py        # Folder navigation and shortcuts
│   ├── browser_control.py    # Voice control of JUDO Browser (CDP path to Edge/Chrome only if configured)
│   ├── file_controller.py    # File system operations
│   ├── file_processor.py     # Document reading and summarization
│   ├── send_message.py       # Messaging integration
│   ├── weather_report.py     # Live weather data
│   ├── flight_finder.py      # Flight search
│   ├── youtube_video.py      # YouTube playback control
│   ├── game_updater.py       # Game update management (Steam / Epic)
│   ├── code_helper.py        # Code review and generation
│   ├── dev_agent.py          # Developer task agent
│   └── desktop.py            # Desktop and taskbar control
├── dashboard/
│   ├── server.py             # FastAPI server behind the remote (phone / QR) dashboard — phone mic relays into the same Live session
│   └── static/                # Dashboard front-end assets
├── memory/
│   ├── memory_manager.py     # Load/save long_term.json — sessions, monitors, identity
│   ├── config_manager.py     # api_keys.json access — key(s), OS, name, voice, gender, colour, toggles
│   ├── long_term.json        # Persistent store: identity, preferences, projects, sessions, monitors
│   ├── human_conversations.jsonl # Every check-in: question, answer, facts learned — conversation training data
│   ├── greetings.json        # Which part of the day JUDO last greeted
│   └── seed_tasks.jsonl      # Seed-generator commands (routing exam)
├── core/
│   ├── prompt.txt            # Assistant personality and tool-routing rules
│   ├── llm_client.py         # Gemini Live session client
│   ├── text_model.py         # Text-model chain (Groq → Gemini/Gemma), multi-key failover, 120 s call limit
│   ├── seed_generator.py     # Writes + cross-checks 1,00,000 real-sounding commands for the routing exam
│   ├── stt.py / tts.py       # Speech-to-text / text-to-speech pipelines
│   ├── undo.py               # One shared undo stack — actions register how to reverse themselves
│   ├── confirm.py            # Irreversible-action gate — the token is issued by the UI, not the model
│   ├── audio_devices.py      # Microphone / speaker list — filtered, measured, resolved by name
│   ├── user_paths.py         # Resolves user directories (Desktop, Documents, …) consistently across OSes
│   ├── plugin_loader.py      # Plugin engine — discovery, validation, crash isolation
│   ├── action_loader.py      # Bundled-action engine — the built-in twin of plugin_loader
│   ├── installer.py          # OS-aware dependency installation helpers
│   └── wake_word.py          # Local "Hey Judo" detector — own thread, offline, opt-in
└── config/
    ├── api_keys.example.json # Template for local API key configuration
    └── api_keys.json         # API key(s), OS setting, assistant name, user name, gender, voice, UI colour, toggles
```

---

## ⚠️ License

Licensed under the **[MIT License](LICENSE)**.

---

## 🤝 Contributing

Issues and pull requests are welcome — bundled **actions** and drop-in **plugins** both follow the self-describing `TOOL` / `PLUGIN` pattern (see `plugins/_template.py`), so adding a new skill rarely touches the core.

---

## 👤 Author

Built and maintained by **[RiteshKumar2e](https://github.com/RiteshKumar2e)**.

⭐ **Star [Ai-Assistant](https://github.com/RiteshKumar2e/Ai-Assistant) if JUDO is useful to you.**
