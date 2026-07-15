"""
Daily check: nudge about "Interesse" prospects that have gone quiet for too
long, so a promising lead never silently falls through the cracks. This is
the piece that turns the "Interesse" button from a dead-end status into an
actual follow-up loop.
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"].strip()

SENT_HISTORY_FILE = "sent_history.json"
REMINDER_AFTER_DAYS = 7


def load_sent_history():
    if not os.path.exists(SENT_HISTORY_FILE):
        return {}
    try:
        with open(SENT_HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_sent_history(history):
    with open(SENT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, sort_keys=True)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown",
    }).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  Telegram: FAILED — network error: {e}")
        return False
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        print(f"  Telegram: FAILED — invalid response: {body[:300]}")
        return False
    ok = result.get("ok", False)
    print(f"  Telegram: {'200 OK' if ok else 'FAILED'} — {result.get('description', 'sent')}")
    return ok


def days_since(iso_str, now):
    try:
        return (now - datetime.fromisoformat(iso_str)).days
    except (TypeError, ValueError):
        return None


def main():
    history = load_sent_history()
    now = datetime.now(timezone.utc)

    stale = []
    for cid, entry in history.items():
        if not isinstance(entry, dict) or entry.get("status") != "interested":
            continue
        since = days_since(entry.get("interested_at"), now)
        if since is None or since < REMINDER_AFTER_DAYS:
            continue
        last_reminded = days_since(entry.get("reminded_at"), now)
        if last_reminded is not None and last_reminded < REMINDER_AFTER_DAYS:
            continue  # already nudged recently — don't spam every day
        stale.append((cid, entry, since))

    if not stale:
        print("No stale 'Interesse' prospect to remind about.")
        return

    lines = "\n".join(f"- {e['name']} (interesse depuis {days}j)" for _, e, days in stale)
    message = (
        "\U0001f514 *Relance prospection*\n"
        f"{len(stale)} prospect(s) marque(s) interesse(s) depuis {REMINDER_AFTER_DAYS}+ jours "
        f"sans mise a jour :\n{lines}\n\nOu en es-tu ?"
    )
    if send_telegram(message):
        now_iso = now.isoformat()
        for _, entry, _ in stale:
            entry["reminded_at"] = now_iso
        save_sent_history(history)


if __name__ == "__main__":
    main()
