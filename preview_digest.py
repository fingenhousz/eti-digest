"""Reconstruct a morning run, capturing messages instead of sending Telegram."""
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from unittest.mock import patch

os.environ['TELEGRAM_BOT_TOKEN'] = 'preview-no-telegram'
os.environ['TELEGRAM_CHAT_ID'] = 'preview-no-telegram'
import alert_policy
import eti_digest
from restore_history import restore


def main():
    instant = datetime.fromisoformat(os.environ['PREVIEW_AT']).astimezone(timezone.utc)
    ref = os.environ['PREVIEW_HISTORY_REF']
    if len(ref) != 40 or any(c not in '0123456789abcdef' for c in ref):
        raise ValueError('Preview history must be an exact commit SHA')
    Path('sent_history.json').write_bytes(subprocess.check_output(['git', 'show', f'{ref}:sent_history.json']))
    restore(ref)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    messages = []
    selection = {}
    original_build = eti_digest.build_digest
    def build(*args, **kwargs):
        result = original_build(*args, **kwargs)
        selection['diagnostics'] = {
            'characters': len(result),
            'parseable_headers': len(eti_digest.extract_company_names(result)),
            'mentions_no_opportunities': any(s in result.casefold() for s in ['aucune opportunit', 'aucun signal', 'aucune entreprise', 'aucun bloc', 'aucun candidat']),
            'has_split_separator': '---SPLIT---' in result,
        }
        return result
    def capture(message, reply_markup=None):
        messages.append(message)
        return True

    with patch.object(eti_digest, 'datetime', Clock), patch.object(alert_policy, 'datetime', Clock), patch.object(eti_digest, 'build_digest', side_effect=build), patch.object(eti_digest, 'send_telegram', side_effect=capture), patch.object(eti_digest, 'save_sent_history'), patch.object(eti_digest.time, 'sleep'):
        eti_digest.main()
    Path('preview.json').write_text(json.dumps({'preview_at': instant.isoformat(), 'history_ref': ref, 'messages': messages, **selection}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Preview complete: {len(messages)} captured messages; no Telegram delivery.')


if __name__ == '__main__':
    main()
