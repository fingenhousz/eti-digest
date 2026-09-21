"""Run: python prospecting_preview.py [--connections private/Connections.csv]."""
import argparse
import json
import os
from pathlib import Path
from datetime import datetime, timezone

from prospecting import (OFFERS, OFFER_PROMPT, PappersClient, budget_assessment,
                         eligible_size, load_connections, network_paths, render_report, resolve_siren)
from alert_policy import company_key, company_keys, excluded_names

FIELDS = ('company', 'offer', 'source_id', 'fact_quote', 'mission', 'buyer_role', 'question')
TOOL = {'name': 'qualify_prospects', 'description': 'Select up to five grounded situations for the three offers.',
        'input_schema': {'type': 'object', 'additionalProperties': False, 'required': ['opportunities'],
                         'properties': {'opportunities': {'type': 'array', 'maxItems': 5,
                            'items': {'type': 'object', 'additionalProperties': False, 'required': list(FIELDS),
                                      'properties': {k: ({'type': 'string', 'enum': list(OFFERS)} if k == 'offer' else
                                                         {'type': 'string', 'minLength': 1, 'maxLength': 600}) for k in FIELDS}}}}}}

EXTRA_QUERIES = [
    ('Diagnostic IA', 'ETI industrie (intelligence artificielle OR productivité OR automatisation)'),
    ('Préparation vente', 'entreprise familiale (prépare transmission OR ouverture capital OR revue stratégique)'),
    ('Adaptation climat', 'industrie usine (sécheresse OR inondation OR chaleur OR restriction eau) France'),
]


def validate(message, sources):
    if message.stop_reason != 'tool_use':
        raise ValueError('Réponse incomplète')
    calls = [b for b in message.content if getattr(b, 'type', None) == 'tool_use']
    if len(calls) != 1 or calls[0].name != TOOL['name']:
        raise ValueError('Réponse structurée attendue')
    data = calls[0].input
    if not isinstance(data, dict) or set(data) != {'opportunities'}:
        raise ValueError('Enveloppe invalide')
    rows = data['opportunities']
    if not isinstance(rows, list) or len(rows) > 5:
        raise ValueError('Sélection invalide')
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            raise ValueError('Champs invalides')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 600 for v in row.values()):
            raise ValueError('Texte invalide')
        if row['offer'] not in OFFERS or row['source_id'] not in sources:
            raise ValueError('Offre ou source inconnue')
        source = sources[row['source_id']]['text']
        normalize = lambda text: ' '.join(text.casefold().split())
        if normalize(row['fact_quote']) not in normalize(source):
            raise ValueError('Citation absente de la source')
        if not company_key(row['company']) or company_key(row['company']) not in company_key(source):
            raise ValueError('Entreprise absente de la source')
        keys = company_keys(row['company'])
        if keys & seen:
            raise ValueError('Entreprise dupliquée')
        seen.update(keys)
    return rows


def select(client, sources, exclusions):
    prompt = OFFER_PROMPT + '\n' + (
        'Sélectionne 0 à 5 situations ; ne force pas le nombre. Retourne une citation exacte du fait. '
        'Une mission est une hypothèse, jamais un besoin avéré. buyer_role est un rôle potentiel, pas un nom inventé. '
        'Priorité aux ETI industrielles et de services ; la taille sera vérifiée après sélection. '
        'Les champs mission et question doivent être spécifiques à la situation. '
        'Respecte les exclusions suivantes : ' + json.dumps(exclusions, ensure_ascii=False) +
        '\nSources : ' + json.dumps(sources, ensure_ascii=False))
    excluded = set().union(*(company_keys(n) for n in exclusions))
    for _ in range(2):
        message = client.messages.create(model='claude-sonnet-4-6', max_tokens=4096,
                                        messages=[{'role': 'user', 'content': prompt}], tools=[TOOL],
                                        tool_choice={'type': 'tool', 'name': TOOL['name'], 'disable_parallel_tool_use': True})
        try:
            return [r for r in validate(message, sources) if not company_keys(r['company']) & excluded]
        except ValueError:
            pass
    raise ValueError('Sélection invalide après deux essais ; aperçu interrompu')


def qualify(rows, sources, pappers, contacts, has_network=False, resolver=resolve_siren):
    result = []
    for selected in rows:
        row = dict(selected)
        source = sources[row['source_id']]
        # A Bodacc source may only donate its SIREN to its own company.
        siren = source.get('siren') if company_key(source.get('company', '')) == company_key(row['company']) else None
        siren = siren or resolver(row['company'])
        company, status = pappers.company(siren)
        row.update(siren=siren, enrichment=status, source=source['reference'],
                   budget=budget_assessment(company), network=network_paths(row['company'], company, contacts),
                   network_status='Aucune correspondance établie' if has_network else 'En attente de l’export LinkedIn')
        # Missing evidence stays in the qualification queue, never a qualified lead.
        row['qualification'] = ('Dépriorisée : confort financier faible' if row['budget']['status'] == 'faible' else
                                'À qualifier : taille non confirmée' if not eligible_size(company) else
                                'À qualifier : budget à confirmer' if row['budget']['status'] != 'favorable' else
                                'Prioritaire pour qualification commerciale')
        result.append(row)
    order = {'Prioritaire pour qualification commerciale': 0, 'À qualifier : budget à confirmer': 1,
             'À qualifier : taille non confirmée': 2, 'Dépriorisée : confort financier faible': 3}
    return sorted(result, key=lambda r: order[r['qualification']])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connections')
    parser.add_argument('--output', default='private/prospecting-preview')
    parser.add_argument('--max-pappers-calls', type=int, choices=range(0, 16), default=5)
    args = parser.parse_args()
    # Import existing collectors without requiring Telegram credentials.
    os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'preview-disabled')
    os.environ.setdefault('TELEGRAM_CHAT_ID', 'preview-disabled')
    import anthropic
    import eti_digest
    from urllib.parse import quote
    eti_digest.RSS_FEEDS = [*eti_digest.RSS_FEEDS, *[
        (label, 'https://news.google.com/rss/search?q=' + quote(query) + '&hl=fr&gl=FR&ceid=FR:fr')
        for label, query in EXTRA_QUERIES]]
    eti_digest.ETI_SIGNAL_WORDS |= {'intelligence artificielle', 'productivité', 'automatisation',
                                  'sécheresse', 'inondation', 'chaleur', 'restriction', 'revue stratégique'}
    contacts = load_connections(args.connections)
    sources = {}
    for i, event in enumerate(eti_digest.fetch_bodacc_events()):
        sources[f'B{i}'] = {'text': event['company'] + ' ' + event.get('content', ''),
                           'company': event['company'], 'siren': event.get('siren'),
                           'reference': f"Bodacc {event.get('date', '')} — SIREN {event.get('siren', '')}"}
    for i, article in enumerate(eti_digest.fetch_rss_news()):
        sources[f'R{i}'] = {'text': article['title'] + ' ' + article.get('summary', ''),
                           'reference': f"{article.get('date', '')} — {article.get('url', '')}"}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    client = PappersClient(os.environ.get('PAPPERS_API_KEY', ''), output / 'pappers_cache.json', args.max_pappers_calls)
    rows = select(anthropic.Anthropic(), sources, excluded_names(eti_digest.load_sent_history())) if sources else []
    qualified = qualify(rows, sources, client, contacts, bool(args.connections))
    diagnostics = {'run_at': datetime.now(timezone.utc).isoformat(),
                   'bodacc_sources': sum(k.startswith('B') for k in sources),
                   'press_sources': sum(k.startswith('R') for k in sources),
                   'selected': len(rows), 'pappers_calls': client.calls}
    (output / 'diagnostics.json').write_text(json.dumps(diagnostics, indent=2), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(qualified, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'report.md').write_text(render_report(qualified), encoding='utf-8')
    print(f'Aperçu terminé : {len(qualified)} pistes ; {client.calls} appels Pappers ; aucun envoi.')


if __name__ == '__main__':
    main()
