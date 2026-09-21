"""Deterministic company identity and freshness checks, independent of the LLM."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

DEDUP_WINDOW_DAYS = 90
RSS_MAX_AGE_HOURS = 72  # Includes Friday's news on Monday.


def company_key(name):
    text = unicodedata.normalize('NFKD', name.casefold())
    text = ''.join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r'\([^)]*\)', '', text)
    tokens = re.findall(r'[a-z0-9]+', text)
    wrappers = {'groupe', 'group', 'sa', 'sas', 'sasu', 'sarl'}
    while tokens and tokens[0] in wrappers:
        tokens.pop(0)
    while tokens and tokens[-1] in wrappers:
        tokens.pop()
    return ''.join(tokens)


def company_keys(name):
    # A combined header must not bypass an exclusion for either company.
    return {key for part in [name, *name.split('/')]
            if (key := company_key(part))}


def as_utc(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def excluded_names(history, now=None):
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=DEDUP_WINDOW_DAYS)
    names = []
    for entry in history.values():
        try:
            recent = as_utc(entry['date']) >= cutoff
        except (KeyError, TypeError, ValueError):
            recent = True  # Uncertain state must not cause a duplicate delivery.
        if recent or entry.get('status') in {'interested', 'pass'}:
            names.append(entry['name'])
    return names


def fresh_article_date(value, now=None):
    try:
        published = parsedate_to_datetime(value)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None
    age = (now or datetime.now(timezone.utc)) - published
    if timedelta(0) <= age <= timedelta(hours=RSS_MAX_AGE_HOURS):
        return published.isoformat()
    return None
