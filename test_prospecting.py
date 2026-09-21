import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from prospecting import (PappersClient, budget_assessment, eligible_size, load_connections,
                         network_paths, render_report, resolve_siren)
from prospecting_preview import qualify, validate


def healthy():
    return {'siren': '123456789', 'denomination': 'Exemple Industrie', 'effectif_min': 300,
            'finances': [dict(date_de_cloture_exercice=f'{year}-12-31', duree_exercice=12,
                              chiffre_affaires=80000000, resultat=2000000,
                              excedent_brut_exploitation=5000000, tresorerie=3000000,
                              dettes_financieres=6000000) for year in (2025, 2024)]}


class QualificationTests(unittest.TestCase):
    def assess(self, data):
        return budget_assessment(data, today=date(2026, 9, 21))

    def test_favorable_requires_complete_evidence(self):
        self.assertEqual(self.assess(healthy())['status'], 'favorable')
        data = healthy()
        del data['finances'][0]['dettes_financieres']
        self.assertEqual(self.assess(data)['status'], 'à confirmer')
        self.assertEqual(self.assess({'chiffre_affaires': 100000000})['status'], 'à confirmer')

    def test_stale_future_and_nonannual_accounts_cannot_be_favorable(self):
        for closing, duration in [('2021-12-31', 12), ('2030-12-31', 12), ('2025-12-31', 18)]:
            data = healthy()
            data['finances'][0].update(date_de_cloture_exercice=closing, duree_exercice=duration)
            self.assertNotEqual(self.assess(data)['status'], 'favorable')

    def test_cash_losses_and_proceedings(self):
        for key, value in [('tresorerie', 0), ('resultat', -1), ('excedent_brut_exploitation', 0)]:
            data = healthy()
            data['finances'][0][key] = value
            self.assertEqual(self.assess(data)['status'], 'faible')
        self.assertEqual(self.assess({'procedure_collective': True})['status'], 'faible')

    def test_non_numbers_and_duplicate_periods(self):
        for value in [True, '5000000', float('nan')]:
            data = healthy()
            data['finances'][0]['excedent_brut_exploitation'] = value
            self.assertEqual(self.assess(data)['status'], 'à confirmer')
        data = healthy()
        data['finances'][1] = data['finances'][0].copy()
        self.assertNotEqual(self.assess(data)['status'], 'favorable')

    def test_stale_revenue_does_not_prove_target_size(self):
        company = {'finances': [{'date_de_cloture_exercice': '2000-12-31', 'chiffre_affaires': 80000000}]}
        self.assertFalse(eligible_size(company))
        self.assertTrue(eligible_size({'effectif_min': 300}))

    def test_api_limit_error_redaction_cache_and_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            client = PappersClient('secret-must-not-leak', Path(folder)/'cache.json', max_calls=1)
            with patch('prospecting.urllib.request.urlopen', side_effect=RuntimeError('secret-must-not-leak')) as fetch:
                data, message = client.company('123456789')
                self.assertEqual(data, {})
                self.assertNotIn('secret', message)
                client.company('987654321')
                self.assertEqual(fetch.call_count, 1)
            client = PappersClient('secret', Path(folder)/'cache.json')
            with patch('prospecting.urllib.request.urlopen') as fetch:
                fetch.return_value.__enter__.return_value.read.return_value = json.dumps(healthy())
                self.assertTrue(client.company('123456789')[0])
                self.assertTrue(client.company('123456789')[0])
                self.assertEqual(fetch.call_count, 1)
                self.assertNotIn('secret', client.path.read_text())
                self.assertFalse(client.company('987654321')[0])

    def test_csv_preamble_and_homonym_are_not_confirmed_relationship(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'Connections.csv'
            path.write_text('Notes:\nExport\n\nFirst Name,Last Name,Company,Position,URL,Email Address\n'
                            'Alice,Martin,Other,CEO,https://example.org,private@example.org\n', encoding='utf-8-sig')
            contacts = load_connections(path)
            self.assertNotIn('Email Address', contacts[0])
            company = {'representants': [{'prenom': 'Alice', 'nom': 'Martin'}]}
            paths = network_paths('Exemple Industrie', company, contacts)
            self.assertIn('homonymie', paths[0]['basis'])
            self.assertEqual(paths[0]['confidence'], 'à confirmer')
            self.assertEqual(network_paths('Unrelated', {}, contacts), [])

    def test_ambiguous_official_identity_stays_unknown(self):
        with patch('prospecting.urllib.request.urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = json.dumps({'results': [
                {'siren': '123456789', 'nom_complet': 'Exemple Industrie'},
                {'siren': '987654321', 'nom_complet': 'Exemple Industrie'}]})
            self.assertIsNone(resolve_siren('Exemple Industrie'))

    def test_structured_provenance_and_offer(self):
        row = {'company': 'Exemple Industrie', 'offer': 'diagnostic_ia', 'source_id': 'R0',
               'fact_quote': 'Exemple Industrie annonce un projet', 'mission': 'Hypothèse',
               'buyer_role': 'DG', 'question': 'Quel périmètre ?'}
        sources = {'R0': {'text': row['fact_quote']}}
        def response(rows):
            return NS(stop_reason='tool_use', content=[NS(type='tool_use', name='qualify_prospects', input={'opportunities': rows})])
        self.assertEqual(validate(response([row]), sources), [row])
        for bad in [{**row, 'fact_quote': 'Invented'}, {**row, 'offer': 'decarbonation'}, {**row, 'company': 'Autre'}]:
            with self.assertRaises(ValueError):
                validate(response([bad]), sources)
        with self.assertRaises(ValueError):
            validate(response([row, row]), sources)

    def test_end_to_end_preview_missing_data_and_wrong_source_company(self):
        row = {'company': 'Exemple Industrie', 'offer': 'diagnostic_ia', 'source_id': 'B0',
               'fact_quote': 'Projet annoncé', 'mission': 'Diagnostic des processus',
               'buyer_role': 'DG', 'question': 'Qui porte le projet ?'}
        sources = {'B0': {'company': 'Autre entreprise', 'siren': '987654321', 'reference': 'Source test'}}
        with tempfile.TemporaryDirectory() as folder:
            client = PappersClient('', Path(folder)/'cache.json')
            rows = qualify([row], sources, client, [], resolver=lambda name: None)
            self.assertIsNone(rows[0]['siren'])
            self.assertEqual(rows[0]['qualification'], 'À qualifier : taille non confirmée')
            self.assertIn('En attente de l’export LinkedIn', render_report(rows))
            self.assertEqual(client.calls, 0)


if __name__ == '__main__':
    unittest.main()
