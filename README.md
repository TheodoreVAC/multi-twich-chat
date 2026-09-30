<div align="center">

# 🌿 Twitch Multi Chat

### One window. Multiple accounts. One shared chat.

<img src="https://img.shields.io/badge/LINUX-052e16?style=for-the-badge&logo=linux&logoColor=86efac" alt="Linux">
<img src="https://img.shields.io/badge/PYTHON-14532d?style=for-the-badge&logo=python&logoColor=bbf7d0" alt="Python 3.13+">
<img src="https://img.shields.io/badge/PYSIDE6-166534?style=for-the-badge&logo=qt&logoColor=dcfce7" alt="PySide6">
<img src="https://img.shields.io/badge/TWITCH_IRC-15803d?style=for-the-badge&logo=twitch&logoColor=white" alt="Twitch IRC">

**A compact Linux desktop client for chatting through several Twitch accounts, without juggling browser tabs.**

</div>

---

## The green bits

| | Feature | What it does |
|:--|:--|:--|
| 🟢 | **Multi-account chat** | Connect several accounts to one channel and switch the sending account from the account list. |
| 🟢 | **Broadcast** | Send one message from every connected account, with an adjustable 0–5000 ms gap (100 ms by default). |
| 🟢 | **Flood mode** | Send randomized prepared messages slowly, with shared repeat protection across accounts. |
| 🟢 | **IRC log** | Inspect incoming and outgoing IRC lines; OAuth login tokens are redacted. |
| 🟢 | **Small, local setup** | Python + PySide6, with account data stored on your machine. |

## Quick start

**Needs:** Linux, Python 3.13+, `python3-venv`, and an internet connection.

From the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

Add an account in the app with its Twitch username and OAuth chat token. The token needs `chat:read` and `chat:edit` scopes.

## Flood mode, at a human pace

The bundled `messages.txt` contains short, general-purpose messages. Flood mode picks randomly from enabled messages allowed for that account.

- Each account waits **4–5 minutes** between its own messages.
- Flood accounts wait at least **15 seconds** between each other's sends.
- A prepared message cannot be reused by any account for at least **5 hours**.
- Repeat history survives restarts in `data/flood_history.json`.
- When an account runs out of available messages, it shows a warning and waits for one to become available.

Edit `messages.txt` to change the texts or assign presets to particular accounts:

```ini
[evening]
text=хороший стрим получился
cooldown=240
accounts=*
repeat_after=5h
enabled=true
```

`accounts=*` applies to every account; otherwise use comma-separated Twitch logins. Cooldowns are in seconds and also accept values such as `5m` or `2h`. Repeat delays in the app are never shorter than five hours.

## Local data & tokens

The app creates its `data/` directory as needed:

| File | Contents |
|:--|:--|
| `accounts.json` | Account usernames and OAuth tokens |
| `account_settings.json` | Per-account mode and connection delay |
| `flood_history.json` | Shared prepared-message send history |

These files are excluded from Git. Tokens are stored locally as plain text, so keep your project directory private and never publish `data/accounts.json`.

## Stack

`Python 3.13+` · `PySide6` · `Twitch IRC over TLS`

No Twitch Developer Console app is needed; chat connects directly through IRC.

---

<div align="center">

**Made with AI. You're on your own.** If something breaks, congratulations: you found the next feature.

<sub>Small app. Multiple bots. One increasingly busy chat.</sub>

</div>
