"""Synchronize public department membership; never infer departures from failed pages."""
import argparse
import csv
import io
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
DEPARTMENTS = {'DENGE': ('933', 'ENGENHARIAS'), 'DCETI': ('932', 'CIÊNCIAS EXATAS'), 'DCH': ('934', 'CIÊNCIAS HUMANAS')}
FIELDS = ['department', 'name', 'id', 'degree', 'email', 'phone', 'room', 'lattes', 'formation', 'status']


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return ' '.join(' '.join(c.text() if isinstance(c, Node) else c for c in self.children).split())

    def find(self, tag=None, cls=None):
        for child in self.children:
            if isinstance(child, Node):
                if (tag is None or child.tag == tag) and (cls is None or cls in child.attrs.get('class', '').split()):
                    yield child
                yield from child.find(tag, cls)


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_department(source, department):
    doc = Document(source).root
    marker = DEPARTMENTS[department][1]
    if not any(marker in n.text().upper() and 'ANGICOS' in n.text().upper() for n in doc.find('h2')):
        raise ValueError(f'{department}: cabeçalho do departamento ausente')
    containers = [n for n in doc.find('div') if n.attrs.get('id') == 'professores']
    totals = re.findall(r'\b(\d+)\s+Docente\(s\)', doc.text(), re.I)
    if len(containers) != 1 or len(totals) != 1:
        raise ValueError(f'{department}: lista ou total ausente/ambíguo')
    records, seen, status = [], set(), ''
    for node in containers[0].find():
        if node.tag == 'h2':
            status = node.text()
        if node.tag != 'table':
            continue
        names = list(node.find('span', 'nome'))
        if len(names) != 1 or not status:
            raise ValueError(f'{department}: cartão sem nome ou vínculo')
        links = list(node.find('a'))
        ids = [parse_qs(urlsplit(a.attrs.get('href', '')).query).get('siape', [''])[0]
               for a in links if urlsplit(a.attrs.get('href', '')).path == '/sigaa/public/docente/portal.jsf']
        if len(ids) != 1 or not ids[0].isdigit() or ids[0] in seen:
            raise ValueError(f'{department}: identificador ausente ou repetido')
        seen.add(ids[0])
        match = re.fullmatch(r'(.+?)(?:\s+\(([^()]*)\))?', names[0].text())
        if not match:
            raise ValueError(f'{department}: nome inválido')
        name, degree = match.groups()
        formation = next((n.text() for n in node.find('span', 'departamento')), '')
        if formation == 'Formação não informada.':
            formation = ''
        lattes = next((a.attrs['href'] for a in links
                       if urlsplit(a.attrs.get('href', '')).hostname in {'lattes.cnpq.br', 'buscatextual.cnpq.br'}
                       and urlsplit(a.attrs.get('href', '')).scheme in {'http', 'https'}), '')
        records.append(dict(department=department, id=ids[0], name=name,
                            degree={'DOUTOR': 'Doutorado', 'MESTRE': 'Mestrado', 'ESPECIALISTA': 'Especialização'}.get(degree, degree or ''),
                            formation=formation, lattes=lattes, status=status))
    if not records or len(records) != int(totals[0]):
        raise ValueError(f'{department}: {len(records)} cartões para total informado de {totals[0]}')
    return records


def fetch(department):
    url = 'https://sigaa.ufersa.edu.br/sigaa/public/departamento/professores.jsf?id=' + DEPARTMENTS[department][0]
    for attempt in range(3):
        try:
            request = Request(url, headers={'User-Agent': 'DocentesAngicos/1.0 (daily public roster check)'})
            with urlopen(request, timeout=40) as response:
                if urlsplit(response.url).hostname != 'sigaa.ufersa.edu.br':
                    raise ValueError('Redirecionamento inesperado')
                source = response.read(2_000_001)
                if len(source) > 2_000_000:
                    raise ValueError('Resposta excessivamente grande')
                return parse_department(source.decode(response.headers.get_content_charset() or 'utf-8'), department)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def reconcile(old, current, state, now):
    """Pure comparison. Require a second valid check >=6h later for removals."""
    old_by_key = {(r['department'], r['id']): r for r in old}
    old_by_id = {r['id']: r for r in old}
    current_by_key = {(r['department'], r['id']): r for r in current}
    for dep in DEPARTMENTS:
        prior = {k for k in old_by_key if k[0] == dep}
        missing = prior - current_by_key.keys()
        if len(missing) > max(3, len(prior) * .2):
            raise ValueError(f'{dep}: muitas ausências simultâneas; revisão necessária, dados preservados')
    rows, added, removed, pending = [], [], [], {}
    for record in current:
        key = record['department'], record['id']
        previous = old_by_key.get(key)
        # Preserve curated biographies, contacts and spelling for existing people.
        row = dict(previous or old_by_id.get(record['id']) or {f: '' for f in FIELDS})
        if not previous:
            row.update(record)
            added.append({'department': key[0], 'id': key[1], 'name': row['name']})
        row['status'] = record['status']
        rows.append(row)
    for key, row in old_by_key.items():
        if key in current_by_key:
            continue
        token = ':'.join(key)
        first = state.get('pending_removals', {}).get(token, now)
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(first)).total_seconds()
        if elapsed >= 6 * 3600:
            removed.append({'department': key[0], 'id': key[1], 'name': row['name']})
        else:
            pending[token] = first
            rows.append(dict(row))
    rows.sort(key=lambda r: (list(DEPARTMENTS).index(r['department']), r['name'].casefold()))
    new_state = dict(state, checked_at=now, pending_removals=pending,
                     counts={dep: sum(r['department'] == dep for r in current) for dep in DEPARTMENTS})
    if added or removed:
        new_state['last_change'] = {'at': now, 'added': added, 'removed': removed}
    return rows, new_state, added, removed


def synchronize(root=ROOT, loader=fetch, dry_run=False):
    with (root / 'data.tsv').open(encoding='utf-8', newline='') as f:
        old = list(csv.DictReader(f, delimiter='\t'))
    state_path = root / 'sync-state.json'
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    # Fetch and validate ALL departments before changing any local file.
    current = [row for dep in DEPARTMENTS for row in loader(dep)]
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    rows, new_state, added, removed = reconcile(old, current, state, now)
    print(json.dumps({'added': added, 'removed': removed, 'pending_removals': new_state['pending_removals'],
                      'counts': new_state['counts'], 'dry_run': dry_run}, ensure_ascii=False, indent=2))
    if not dry_run:
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=FIELDS, delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
        (root / 'data.tsv').write_text(output.getvalue(), encoding='utf-8')
        state_path.write_text(json.dumps(new_state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Consultar sem alterar arquivos')
    args = parser.parse_args()
    synchronize(dry_run=args.dry_run)
