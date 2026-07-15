"""
Poll Telegram for Interesse/Pass button clicks on ETI company blocks and
persist the chosen pipeline status into sent_history.json. Runs on a short
interval (via a separate workflow) since GitHub Actions has no way to
receive Telegram's webhook push directly.
"""

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"].strip()

OFFSET_FILE = "telegram_offset.json"
SENT_HISTORY_FILE = "sent_history.json"
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Deliberately different symbol family from the "✅ Interesse" action
# button — a lock reads clearly as "figé, plus d'action possible" instead of
# looking like a slight variant of the same checkmark. Text spells out what
# actually happened (captured into the pipeline) instead of just echoing
# the button's own label back.
STATUS_LABELS = {"interested": "\U0001f512 Interet enregistre", "pass": "\U0001f512 Ignore"}


def load_offset():
    if not os.path.exists(OFFSET_FILE):
        return 0
    try:
        with open(OFFSET_FILE, encoding="utf-8") as f:
            return json.load(f).get("offset", 0)
    except (json.JSONDecodeError, OSError):
        return 0


def save_offset(offset):
    with open(OFFSET_FILE, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f)


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


def api_call(method, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    try:
        with urllib.request.urlopen(f"{API}/{method}", data=data, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8", errors="ignore"))


def answer_callback(callback_query_id, text, alert=False):
    api_call("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text,
        "show_alert": "true" if alert else "false",
    })


def clear_buttons(chat_id, message_id, label):
    api_call("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": json.dumps({"inline_keyboard": [[{"text": label, "callback_data": "noop"}]]}),
    })


def main():
    offset = load_offset()
    result = api_call("getUpdates", {"offset": offset, "timeout": 0})
    if not result.get("ok"):
        print(f"getUpdates failed: {result}")
        return

    updates = result.get("result", [])
    print(f"{len(updates)} update(s) since offset {offset}")
    if not updates:
        return

    history = load_sent_history()
    history_changed = False

    for update in updates:
        offset = max(offset, update["update_id"] + 1)
        cq = update.get("callback_query")
        if not cq:
            continue

        data = cq.get("data", "")
        cq_id = cq["id"]
        if data == "noop":
            answer_callback(cq_id, "")
            continue
        if not data.startswith("pipeline:"):
            continue

        _, cid, action = data.split(":", 2)
        entry = history.get(cid)
        if not entry:
            answer_callback(cq_id, "Entreprise expiree ou hors historique.", alert=True)
            continue

        if isinstance(entry, str):
            answer_callback(cq_id, "Entree au format legacy, non modifiable.", alert=True)
            continue

        entry["status"] = action
        if action == "interested" and not entry.get("interested_at"):
            # Marks when the clock starts for the follow-up reminder job —
            # only set once, so re-clicking doesn't reset the countdown.
            entry["interested_at"] = datetime.now(timezone.utc).isoformat()
        history_changed = True
        label = STATUS_LABELS.get(action, action)
        answer_callback(cq_id, label)

        message = cq.get("message") or {}
        chat = message.get("chat") or {}
        if message.get("message_id") and chat.get("id"):
            clear_buttons(chat["id"], message["message_id"], label)

        print(f"  {entry.get('name', cid)} -> {action}")

    if history_changed:
        save_sent_history(history)
    save_offset(offset)


if __name__ == "__main__":
    main()
