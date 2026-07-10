"""
ETI Digest → Telegram
Fetches ETI signals from Bodacc + press RSS, selects 3-5 prospects with Claude,
sends a daily prospection briefing to Telegram.
"""

import os
import re
import sys
import json
import time
import hashlib
import urllib.request
import urllib.parse
import urllib.error
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import feedparser
import anthropic

APOSTROPHE_RE = re.compile("[‘’‚‛ʼʻ′‵]")


def normalize_apostrophes(text):
    return APOSTROPHE_RE.sub("'", text)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"].strip()
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"].strip()
PAPPERS_API_KEY = os.environ.get("PAPPERS_API_KEY", "").strip()

SENT_HISTORY_FILE = "sent_history.json"
DEDUP_WINDOW_DAYS = 14


def company_id(name):
    """Short, stable id for a company — used as both the sent_history key and
    the Telegram callback_data for the Interesse/Pass buttons (callback_data
    is capped at 64 bytes, so the full company name can't be used there)."""
    return hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:12]


def load_sent_history():
    """Returns {company_id: {"name", "date", "status", "sector"}}, pruned to
    the dedup window. Transparently migrates the legacy {name: date_str}
    format from before pipeline-status tracking existed."""
    if not os.path.exists(SENT_HISTORY_FILE):
        return {}
    try:
        with open(SENT_HISTORY_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)
    pruned = {}
    for key, value in raw.items():
        if isinstance(value, str):
            name, date_str, status, sector = key, value, "pending", None
            cid = company_id(name)
        else:
            cid = key
            name = value.get("name", key)
            date_str = value.get("date", "")
            status = value.get("status", "pending")
            sector = value.get("sector")
        try:
            if date_str and datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc) >= cutoff:
                pruned[cid] = {"name": name, "date": date_str, "status": status, "sector": sector}
        except ValueError:
            continue
    return pruned


def save_sent_history(history):
    with open(SENT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, sort_keys=True)


BLOCK_HEADER_RE = re.compile(r"\*([^*]+)\*")


def extract_company_names(digest_text):
    """Pull company names out of '*Emoji Nom entreprise*' block headers
    (strips the leading emoji token)."""
    names = []
    for raw in BLOCK_HEADER_RE.findall(digest_text):
        parts = raw.strip().split(None, 1)
        name = parts[1].strip() if len(parts) == 2 else (parts[0].strip() if parts else "")
        if name:
            names.append(name)
    return names


SECTOR_RE = re.compile(r"Secteur\s*:\s*(.+)")


def extract_sector(block):
    """Pull the 'Secteur : ...' line out of a single company block, or None
    if absent (e.g. Claude omitted it)."""
    match = SECTOR_RE.search(block)
    return match.group(1).strip() if match else None

BODACC_BASE = "https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets"

# Google News RSS — works server-side, no auth needed
RSS_FEEDS = [
    ("Google News", "https://news.google.com/rss/search?q=cession+transmission+entreprise+France&hl=fr&gl=FR&ceid=FR:fr"),
    ("Google News", "https://news.google.com/rss/search?q=rachat+acquisition+PME+ETI+France&hl=fr&gl=FR&ceid=FR:fr"),
    ("Google News", "https://news.google.com/rss/search?q=redressement+judiciaire+entreprise+France&hl=fr&gl=FR&ceid=FR:fr"),
    ("Google News", "https://news.google.com/rss/search?q=changement+dirigeant+PDG+entreprise+France&hl=fr&gl=FR&ceid=FR:fr"),
    # Site-restricted queries targeting business/finance press directly — raw
    # RSS feeds from these publishers block scraping (403), but Google News
    # indexes and serves their articles fine via a site: search, and this
    # skews strongly toward genuine mid/large-cap deals rather than the
    # TPE-heavy noise from Bodacc.
    ("Les Echos / La Tribune", "https://news.google.com/rss/search?q=(cession+OR+rachat+OR+LBO+OR+fusion)+ETI+France+site:lesechos.fr+OR+site:latribune.fr&hl=fr&gl=FR&ceid=FR:fr"),
    ("Capital / Usine Nouvelle", "https://news.google.com/rss/search?q=(rachat+OR+acquisition+OR+cession)+groupe+France+site:capital.fr+OR+site:usinenouvelle.com&hl=fr&gl=FR&ceid=FR:fr"),
    ("Private equity", "https://news.google.com/rss/search?q=private+equity+OR+LBO+ETI+France+millions+CA&hl=fr&gl=FR&ceid=FR:fr"),
]

ETI_SIGNAL_WORDS = {
    "cession", "transmission", "rachat", "acquisition", "reprise",
    "redressement", "liquidation", "sauvegarde", "restructur",
    "dirigeant", "pdg", "directeur general", "president",
    "actionnaire", "capital", "lbo", "private equity",
    "fusion", "rapprochement", "nouveau directeur",
}


def _extract_bodacc_record(record):
    name = record.get("commercant") or ""
    registre = record.get("registre") or []
    siren = registre[0].replace(" ", "") if registre else ""
    city = record.get("ville") or ""
    famille = record.get("familleavis_lib") or record.get("familleavis") or ""
    content = record.get("acte") or record.get("jugement") or record.get("modificationsgenerales") or ""
    if isinstance(content, dict):
        content = json.dumps(content, ensure_ascii=False)
    return name.strip(), siren, city.strip(), famille, str(content)[:300]


def fetch_bodacc_events():
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")
    events = []

    # familleavis: collective=procédures collectives (redressement/liquidation), conciliation=difficulté
    # 'vente' retiré : concerne quasi-exclusivement des fonds de commerce TPE
    where = f"dateparution >= date'{since}' AND (familleavis='collective' OR familleavis='conciliation')"

    try:
        params = urllib.parse.urlencode({
            "where": where,
            "limit": 80,
            "order_by": "dateparution DESC",
            "select": "commercant,ville,registre,familleavis,familleavis_lib,dateparution,acte,jugement",
        })
        url = f"{BODACC_BASE}/annonces-commerciales/records?{params}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        for record in data.get("results", []):
            name, siren, city, famille, content = _extract_bodacc_record(record)
            if not name:
                continue
            events.append({
                "type": famille,
                "company": name,
                "siren": siren,
                "city": city,
                "date": record.get("dateparution", ""),
                "content": content,
            })
    except Exception as e:
        print(f"  Bodacc error: {e}")

    return events


def fetch_rss_news():
    articles = []
    for source_name, url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:40]:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                combined = (title + " " + summary).lower()
                if any(word in combined for word in ETI_SIGNAL_WORDS):
                    articles.append({
                        "source": source_name,
                        "title": title,
                        "summary": summary[:400],
                        "date": entry.get("published", ""),
                    })
        except Exception as e:
            print(f"  RSS {source_name} error: {e}")
    return articles


CA_MIN = 50_000_000
CA_MAX = 200_000_000
PAPPERS_CALLS_MAX = 30


def check_pappers(siren):
    """Returns (ca_millions, effectif) or (None, None) if unavailable."""
    if not PAPPERS_API_KEY or not siren:
        return None, None
    try:
        params = urllib.parse.urlencode({
            "api_token": PAPPERS_API_KEY,
            "siren": siren,
        })
        url = "https://api.pappers.fr/v2/entreprise?" + params
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        ca = data.get("chiffre_affaires")
        effectif = data.get("effectif") or data.get("tranche_effectif_salarie")
        ca_m = round(ca / 1_000_000, 1) if ca else None
        if ca_m:
            print(f"    Pappers {siren}: {ca_m}M€ CA")
        else:
            print(f"    Pappers {siren}: pas de CA ({data.get('denomination', '?')})")
        return ca_m, effectif
    except urllib.error.HTTPError as e:
        print(f"    Pappers {siren}: HTTP {e.code}")
        return None, None
    except Exception as ex:
        print(f"    Pappers {siren}: erreur {ex}")
        return None, None


def filter_with_pappers(events):
    """Enrich events with CA from Pappers, filter out confirmed non-ETIs."""
    if not PAPPERS_API_KEY:
        return events

    calls = 0
    filtered = []
    for e in events:
        if calls >= PAPPERS_CALLS_MAX:
            # Keep remaining without validation rather than silently dropping them
            filtered.append(e)
            continue
        siren = e.get("siren", "")
        if not siren:
            filtered.append(e)
            continue
        ca, effectif = check_pappers(siren)
        calls += 1
        if ca is not None:
            if CA_MIN <= ca * 1_000_000 <= CA_MAX:
                e["ca"] = ca
                e["effectif"] = effectif
                filtered.append(e)
            # else: confirmed non-ETI → drop silently
        else:
            # No CA data, but effectif alone can still be a strong ETI signal
            # (was previously discarded here even when Pappers returned it)
            if effectif:
                e["effectif"] = effectif
            filtered.append(e)

    print(f"  Pappers: {calls} calls, {len(filtered)}/{len(events)} events kept")
    return filtered


def build_digest(bodacc_events, rss_articles, excluded_companies=None):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def fmt_event(e):
        ca_str = str(e.get("ca", "")) + "M€ CA" if e.get("ca") else "CA non vérifié"
        effectif_str = f", effectif Pappers: {e['effectif']}" if e.get("effectif") else ""
        return "- [{}] {} ({}) | {}{} | SIREN {} : {}".format(
            e.get("type", ""), e.get("company", ""), e.get("city", ""),
            ca_str, effectif_str, e.get("siren", ""), e.get("content", "")
        )

    bodacc_text = "\n".join(fmt_event(e) for e in bodacc_events) or "Aucune annonce Bodacc aujourd’hui."
    rss_text = "\n".join(
        "- [{}] {} - {}".format(e.get("source", ""), e.get("title", ""), e.get("summary", ""))
        for e in rss_articles
    ) or "Aucun article presse aujourd’hui."

    excluded_companies = excluded_companies or []
    exclusion_block = (
        "\nEntreprises DEJA envoyees ces {} derniers jours — EXCLUSION ABSOLUE, aucune "
        "exception : ne les reselectionne sous aucun pretexte, meme si un nouveau signal "
        "Bodacc/presse les mentionne a nouveau (procedure en plusieurs etapes, relance "
        "presse, etc). Traite-les comme si elles n'existaient pas dans les signaux du jour "
        ":\n{}\n".format(DEDUP_WINDOW_DAYS, ", ".join(excluded_companies))
        if excluded_companies else ""
    )

    prompt = f"""Tu es un expert en développement commercial B2B ciblant les ETI françaises (250-4999 salariés, 50M€-1,5Md€ de CA).

Voici les signaux du jour. Les entreprises listées ont été pré-filtrées : celles avec un CA vérifié sont dans la fourchette 50-200M€. Les autres ont un CA non vérifié.
{exclusion_block}
## Annonces Bodacc (24 dernières heures)
{bodacc_text}

## Presse spécialisée
{rss_text}

Sélectionne les 3 à 5 meilleures opportunités de prospection parmi ces signaux.

Critères : moment de vie fort (transmission, cession, procédure collective, fusion, changement de dirigeant), fenêtre de prospection ouverte, entreprise de taille ETI.

REGLE DE TAILLE (stricte) : si le CA n'est pas vérifié, ne selectionne l'entreprise QUE si le texte source contient un indice fort et explicite de taille ETI (effectif >= 250 salaries mentionne, chiffre d'affaires mentionne dans le texte, groupe/filiale connue, notoriete manifeste). En cas de doute sur la taille, EXCLUS l'entreprise plutot que de la retenir — mieux vaut 2 opportunites solides que 5 dont certaines sont des PME/TPE.

REGLE DE TEXTE : chaque bloc doit etre 100% autoporteur (un lecteur qui ne voit que ce bloc doit tout comprendre, sans avoir besoin des autres messages) et rediger avec des phrases completes, sans pronom sans antecedent dans le meme bloc.

IMPORTANT : réponds UNIQUEMENT avec les blocs ETI, sans introduction ni conclusion. Format strict :

*[Emoji] [Nom entreprise]* — [Ville] | [CA]M€
Secteur : [secteur d'activite en 1-3 mots, ex: "Distribution", "BTP", "Agroalimentaire"]
Signal : [4-6 mots]
Contexte : [1 phrase]
Opportunité : [1 phrase]

Sépare chaque bloc par "---SPLIT---" seul sur sa ligne. Apostrophes droites uniquement (').
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text
    return normalize_apostrophes(text)


def send_telegram(message, reply_markup=None):
    """Send a message via the Telegram bot.

    Telegram's API gives a proper JSON {"ok": bool, ...} response with a
    real HTTP status — unlike CallMeBot, no HTML-body-sniffing needed.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    if reply_markup is not None:
        payload["reply_markup"] = json.dumps(reply_markup)
    data = urllib.parse.urlencode(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(url, data=data, timeout=30) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        status = e.code
        body = e.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"  Telegram: FAILED — network error: {e}")
        return False

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        print(f"  Telegram: FAILED (HTTP {status}) — invalid response: {body[:300]}")
        return False

    if result.get("ok"):
        print(f"  Telegram: {status} OK — message sent ({len(message)} chars)")
        return True
    print(f"  Telegram: FAILED (HTTP {status}) — {result.get('description', body[:300])}")
    return False


def detect_sector_patterns(history, todays_names):
    """Flag sectors where >=2 companies within the dedup window share a
    sector, when today's fresh selection contributes at least one of them —
    a stronger prospecting signal than an isolated hit, and this ensures the
    alert fires once per new contribution rather than repeating stale news."""
    by_sector = defaultdict(list)
    display_name = {}
    for v in history.values():
        sector = (v.get("sector") or "").strip()
        if not sector:
            continue
        key = sector.lower()
        display_name.setdefault(key, sector)
        by_sector[key].append(v["name"])

    patterns = []
    for key, names in by_sector.items():
        unique_names = sorted(set(names))
        if len(unique_names) < 2:
            continue
        if not any(n in todays_names for n in unique_names):
            continue
        patterns.append((display_name[key], unique_names))
    return patterns


def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching Bodacc events...")
    bodacc_events = fetch_bodacc_events()
    print(f"  {len(bodacc_events)} events")

    print("Fetching RSS news...")
    rss_articles = fetch_rss_news()
    print(f"  {len(rss_articles)} relevant articles")

    if not bodacc_events and not rss_articles:
        print("No data — skipping.")
        return

    print("Filtering with Pappers...")
    bodacc_events = filter_with_pappers(bodacc_events)

    history = load_sent_history()
    excluded_names = [v["name"] for v in history.values()]
    print(f"  {len(history)} companie(s) sent in the last {DEDUP_WINDOW_DAYS} days, excluded from re-selection")

    print("Building digest with Claude...")
    digest = build_digest(bodacc_events, rss_articles, excluded_companies=excluded_names)

    blocks = [b.strip() for b in digest.split("---SPLIT---") if b.strip()]

    # Mechanical safety net: don't just rely on the prompt instruction — if
    # Claude re-selects an excluded company anyway (e.g. a multi-step legal
    # procedure reads as "new"), drop that block before it's ever sent.
    excluded_lower = {name.lower() for name in excluded_names}
    kept_blocks = []
    for block in blocks:
        names = extract_company_names(block)
        if names and names[0].lower() in excluded_lower:
            print(f"  Dropping block for '{names[0]}' — already sent within the last {DEDUP_WINDOW_DAYS} days")
            continue
        kept_blocks.append(block)
    blocks = kept_blocks

    date_str = datetime.now().strftime("%d %B %Y")
    header = f"\U0001f3af *ETI du {date_str}* — {len(blocks)} opportunités"

    print(f"Sending {len(blocks) + 1} Telegram messages...")
    failures = 0 if send_telegram(header) else 1

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    todays_names = []
    for i, block in enumerate(blocks):
        time.sleep(3)
        tagged_block = f"\U0001f3af {block}"
        print(f"  [{i+1}/{len(blocks)}] {block[:60]}...")

        names = extract_company_names(block)
        name = names[0] if names else None
        reply_markup = None
        if name:
            todays_names.append(name)
            cid = company_id(name)
            history[cid] = {
                "name": name, "date": today_str,
                "status": "pending", "sector": extract_sector(block),
            }
            # Only "Interesse" — by definition the other outcome is "no
            # action taken", not a state worth a button of its own.
            reply_markup = {"inline_keyboard": [[
                {"text": "✅ Interesse", "callback_data": f"pipeline:{cid}:interested"},
            ]]}

        if not send_telegram(tagged_block, reply_markup=reply_markup):
            failures += 1

    save_sent_history(history)

    for sector, names in detect_sector_patterns(history, todays_names):
        time.sleep(3)
        pattern_msg = (
            f"\U0001f4ca *Pattern sectoriel detecte : {sector}*\n"
            f"{len(names)} entreprises de ce secteur signalees en {DEDUP_WINDOW_DAYS} jours : "
            f"{', '.join(names)} — signal de consolidation, opportunite de prospection elargie sur ce secteur."
        )
        if not send_telegram(pattern_msg):
            failures += 1

    if failures:
        print(
            f"\nERROR: {failures} Telegram message(s) were NOT delivered "
            "(see responses above). Common causes: invalid bot token, "
            "wrong chat ID, or the bot was blocked."
        )
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
