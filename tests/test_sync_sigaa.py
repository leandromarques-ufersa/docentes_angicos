import csv
import json
import tempfile
import unittest
from pathlib import Path
from sync_sigaa import FIELDS, parse_department, reconcile, synchronize


def page(cards=None, total=1):
    cards = cards if cards is not None else '''
      <table><tr><td class="descricao"><span class="nome">ANA &amp; SILVA (DOUTOR)</span>
      <span class="departamento">Formação não informada.</span>
      <span class="pagina"><a href="/sigaa/public/docente/portal.jsf?siape=123">Ver página</a></span>
      </td></tr></table>'''
    return f'''<h2>DEPARTAMENTO DE ENGENHARIAS - ANGICOS</h2>
      <div id="professores"><h1>Corpo Docente</h1><h2>Professor Substituto</h2>{cards}</div>
      <table><tfoot><td><b>{total} Docente(s)</b></td></tfoot></table>'''


def row(dep='DENGE', id='123', name='Ana'):
    return dict({f: '' for f in FIELDS}, department=dep, id=id, name=name,
                status='Ativo Permanente', email='ana@example.org', formation='Biografia revisada')


class ParsingTests(unittest.TestCase):
    def test_real_structure_and_entities(self):
        parsed = parse_department(page(), 'DENGE')[0]
        self.assertEqual(parsed['name'], 'ANA & SILVA')
        self.assertEqual(parsed['degree'], 'Doutorado')
        self.assertEqual(parsed['id'], '123')
        self.assertEqual(parsed['status'], 'Professor Substituto')
        self.assertEqual(parsed['formation'], '')

    def test_empty_truncated_wrong_department_and_duplicate_fail(self):
        for source in (page('', 0), page(total=2), '<h1>Manutenção</h1>',
                       page().replace('ENGENHARIAS', 'OUTRO'),
                       page().replace('</div>', page().split('<h2>Professor Substituto</h2>')[1].split('</div>')[0] + '</div>')):
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_department(source, 'DENGE')

    def test_missing_id_fails(self):
        with self.assertRaises(ValueError):
            parse_department(page().replace('siape=123', 'other=123'), 'DENGE')


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.old = [row(), row('DCETI', '456'), row('DCH', '789')]
        self.now = '2026-09-13T03:00:00+00:00'

    def test_addition_and_preserved_curated_fields(self):
        current = [dict(r, formation='Texto abreviado', email='') for r in self.old]
        current.append(row(id='321', name='Novo docente'))
        rows, state, added, removed = reconcile(self.old, current, {}, self.now)
        ana = next(r for r in rows if r['id'] == '123')
        self.assertEqual(ana['formation'], 'Biografia revisada')
        self.assertEqual(ana['email'], 'ana@example.org')
        self.assertEqual([r['id'] for r in added], ['321'])
        self.assertEqual(removed, [])

    def test_removal_needs_second_check_and_elapsed_time(self):
        current = self.old[1:]
        rows, state, _, removed = reconcile(self.old, current, {}, self.now)
        self.assertEqual(len(rows), 3)
        self.assertEqual(removed, [])
        rows, _, _, removed = reconcile(rows, current, state, '2026-09-13T03:01:00+00:00')
        self.assertEqual(removed, [])
        rows, state, _, removed = reconcile(rows, current, state, '2026-09-14T03:00:00+00:00')
        self.assertEqual([r['id'] for r in removed], ['123'])
        self.assertEqual(state['pending_removals'], {})

    def test_reappearance_cancels_pending_removal(self):
        _, state, _, _ = reconcile(self.old, self.old[1:], {}, self.now)
        _, state, _, removed = reconcile(self.old, self.old, state, '2026-09-14T03:00:00+00:00')
        self.assertEqual(state['pending_removals'], {})
        self.assertEqual(removed, [])

    def test_transfer_keeps_contacts(self):
        current = [dict(self.old[0], department='DCH')] + self.old[1:]
        rows, state, added, _ = reconcile(self.old, current, {}, self.now)
        moved = next(r for r in rows if r['id'] == '123' and r['department'] == 'DCH')
        self.assertEqual(moved['email'], 'ana@example.org')
        self.assertEqual(added[0]['department'], 'DCH')
        self.assertIn('DENGE:123', state['pending_removals'])

    def test_large_drop_fails(self):
        old = [row(id=str(i)) for i in range(20)]
        with self.assertRaises(ValueError):
            reconcile(old, old[:10], {}, self.now)

    def test_failed_department_does_not_write_any_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (root / 'data.tsv').open('w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter='\t')
                writer.writeheader()
                writer.writerows(self.old)
            (root / 'sync-state.json').write_text('{}', encoding='utf-8')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            def loader(dep):
                if dep == 'DCH':
                    raise ValueError('SIGAA indisponível')
                return [r for r in self.old if r['department'] == dep]
            with self.assertRaises(ValueError):
                synchronize(root, loader)
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})


if __name__ == '__main__':
    unittest.main()
