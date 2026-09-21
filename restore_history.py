"""Recover pruned companies using this repository's Git history.

Safe to run before each digest. Current records always take precedence.
"""
import hashlib
import json
from pathlib import Path
import subprocess


def restore(ref='HEAD'):
    path = Path('sent_history.json')
    history = json.loads(path.read_text(encoding='utf-8'))
    original_count = len(history)
    revisions = subprocess.check_output(
        ['git', 'log', ref, '--since=90 days ago', '--format=%H', '--', str(path)], text=True
    ).splitlines()
    for revision in revisions:
        old = json.loads(subprocess.check_output(
            ['git', 'show', f'{revision}:{path.name}'], encoding='utf-8'
        ))
        for key, value in old.items():
            if isinstance(value, str):
                value = {'name': key, 'date': value, 'status': 'pending', 'sector': None}
                key = hashlib.sha1(key.strip().lower().encode('utf-8')).hexdigest()[:12]
            history.setdefault(key, value)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(history, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)
    print(f'Recovered {len(history) - original_count} companies; {len(history)} total')


if __name__ == '__main__':
    restore()
