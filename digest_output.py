"""Validate structured selections and render company cards deterministically."""
import re

FIELDS = ('company', 'city', 'revenue', 'sector', 'dirigeant', 'signal', 'context', 'opportunity', 'source_id')
SELECTION_TOOL = {
    'name': 'select_opportunities',
    'description': 'Return zero to five grounded ETI prospects; empty opportunities means no eligible prospect.',
    'input_schema': {
        'type': 'object', 'additionalProperties': False,
        'required': ['opportunities'],
        'properties': {'opportunities': {
            'type': 'array', 'maxItems': 5,
            'items': {'type': 'object', 'additionalProperties': False,
                      'required': list(FIELDS),
                      'properties': {key: {'type': 'string', 'minLength': 1, 'maxLength': 800} for key in FIELDS}}
        }}
    }
}


class InvalidSelection(ValueError):
    pass


def clean(value):
    # The renderer owns markup and separators; model values are plain text.
    return re.sub(r'\s+', ' ', re.sub(r'[*_`\[\]]', '', value)).replace('---SPLIT---', ' ').strip()


def render_selection(message, sources):
    if message.stop_reason != 'tool_use':
        raise InvalidSelection('Selection did not finish with a complete tool response')
    calls = [b for b in message.content if getattr(b, 'type', None) == 'tool_use']
    if len(calls) != 1 or calls[0].name != SELECTION_TOOL['name']:
        raise InvalidSelection('Expected exactly one selection response')
    data = calls[0].input
    if not isinstance(data, dict) or set(data) != {'opportunities'}:
        raise InvalidSelection('Invalid selection envelope')
    rows = data['opportunities']
    if not isinstance(rows, list) or len(rows) > 5:
        raise InvalidSelection('Invalid selection count')
    blocks = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise InvalidSelection('Missing or unexpected company fields')
        if any(not isinstance(v, str) or not clean(v) or len(v) > 800 for v in row.values()):
            raise InvalidSelection('Invalid company field')
        if row['source_id'] not in sources:
            raise InvalidSelection('Unknown source reference')
        r = {k: clean(v) for k, v in row.items()}
        if not re.search(r'\w', r['company']):
            raise InvalidSelection('Invalid company name')
        blocks.append(
            f"*🏭 {r['company']}* — {r['city']} | {r['revenue']}\n"
            f"Secteur : {r['sector']}\nDirigeant : {r['dirigeant']}\n"
            f"Signal : {r['signal']}\nContexte : {r['context']}\n"
            f"Opportunité : {r['opportunity']}\nSource : {sources[row['source_id']]}"
        )
    return '\n---SPLIT---\n'.join(blocks)
