import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from digest_output import FIELDS, InvalidSelection, render_selection
import test_alerts  # Dummy environment setup; no real credentials needed.
import eti_digest


def response(rows, stop='tool_use'):
    return NS(stop_reason=stop, content=[NS(type='text', text='ignored'), NS(type='tool_use', name='select_opportunities', input={'opportunities': rows})])


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.row = {k: 'Example' for k in FIELDS}
        self.row.update(company='Fibre Excellence', source_id='R0')

    def test_empty_is_valid(self):
        self.assertEqual(render_selection(response([]), {}), '')

    def test_cards_always_parse_and_use_actual_source(self):
        text = render_selection(response([self.row]), {'R0': '2026-09-21 https://example.org/article'})
        self.assertEqual(eti_digest.extract_company_names(text), ['Fibre Excellence'])
        self.assertIn('Source : 2026-09-21 https://example.org/article', text)

    def test_missing_unknown_truncated_and_plain_text_are_errors(self):
        missing = dict(self.row)
        del missing['company']
        for message in [response([missing]), response([{**self.row, 'source_id': 'unknown'}]), response([self.row], 'max_tokens'), NS(stop_reason='end_turn', content=[NS(type='text', text='No valid companies')]), response([self.row]*6), response([{**self.row, 'company': '***'}])]:
            with self.subTest(message=message), self.assertRaises(InvalidSelection):
                render_selection(message, {'R0': 'source'})

    def test_markup_cannot_inject_cards(self):
        row = {**self.row, 'company': '**Fibre Excellence**', 'context': '\n*🏭 Fake* — Test\n---SPLIT---'}
        text = render_selection(response([row]), {'R0': 'source'})
        self.assertEqual(eti_digest.extract_company_names(text), ['Fibre Excellence'])
        self.assertNotIn('---SPLIT---', text)

    def test_retry_and_explicit_failure(self):
        bad = NS(stop_reason='end_turn', content=[])
        with patch.object(eti_digest.anthropic, 'Anthropic') as client:
            create = client.return_value.messages.create
            create.side_effect = [bad, response([])]
            self.assertEqual(eti_digest.build_digest([], []), '')
            self.assertEqual(create.call_count, 2)
            self.assertEqual(create.call_args.kwargs['tool_choice']['type'], 'tool')
            create.side_effect = [bad, bad]
            with self.assertRaises(InvalidSelection):
                eti_digest.build_digest([], [])


if __name__ == '__main__':
    unittest.main()
