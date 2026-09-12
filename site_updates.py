"""Public freshness metadata and a version tied to content, not scheduled checks."""
import hashlib
import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent


def prepare():
    digest = hashlib.sha256()
    for name in ('data.tsv', 'photos.json', 'status.json', 'build.py', 'site_updates.py', 'dist/style.css', 'dist/updates.js'):
        digest.update(name.encode())
        digest.update((ROOT / name).read_bytes())
    state_path = ROOT / 'sync-state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    metadata = {'version': digest.hexdigest()[:20], 'checked_at': state.get('checked_at'),
                'last_change': state.get('last_change'),
                'pending_removals': len(state.get('pending_removals', {}))}
    (ROOT / 'dist/version.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return metadata


def footer_status(metadata):
    checked = metadata['checked_at']
    if not checked:
        return 'Listas: checagem automática ainda não realizada.'
    local = datetime.fromisoformat(checked).astimezone(timezone(timedelta(hours=-3)))
    text = 'Listas verificadas em ' + local.strftime('%d/%m/%Y às %H:%M') + ' (Fortaleza).'
    if metadata['pending_removals']:
        text += ' Algumas ausências aguardam confirmação.'
    return html.escape(text)


def script(metadata, root):
    return '<script defer src="' + root + 'updates.js" data-version="' + metadata['version'] + '"></script>'
