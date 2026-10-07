"""Génère conferences.ics à partir des agendas de conférences.
v1 : source Collège de France uniquement. D'autres sources s'ajoutent
en écrivant une fonction de plus et en l'ajoutant à SOURCES.
"""
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

PARIS = ZoneInfo("Europe/Paris")
HEADERS = {"User-Agent": "Mozilla/5.0 (calendrier-conferences perso)"}

# Filtre par mots-clés. Laisser vide = tout garder (recommandé pour la v1).
KEYWORDS = []  # ex : ["cognit", "cerveau", "esprit", "neuro", "philosophie"]

CDF_BASE = "https://www.college-de-france.fr"
CDF_PAGES = 6  # nombre de pages d'agenda à lire (30 événements par page)


def parse_dt(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(PARIS)


def get_cards(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser").select(".card-event")


def txt(card, selector):
    el = card.select_one(selector)
    return el.get_text(strip=True) if el else ""


def make_event(card, parent="", default_url=""):
    """Transforme une carte en événement, ou None si elle n'a pas d'horaire propre."""
    times = card.select(".card-event__time--desktop time[datetime]")
    link = card.select_one("a.card-event__link")
    title = txt(card, ".card-event__title-decorator")
    if not times or not title:
        return None
    start = parse_dt(times[0]["datetime"])
    end = parse_dt(times[1]["datetime"]) if len(times) > 1 else start + timedelta(hours=1)
    if end <= start:
        end = start + timedelta(hours=1)
    url = CDF_BASE + link["href"] if link else default_url
    desc = [
        f"Colloque : {parent}" if parent else "",
        f"Type : {txt(card, '.card-event__type')}" if txt(card, ".card-event__type") else "",
        f"Intervenant : {txt(card, '.card-event__main-speaker')}" if txt(card, ".card-event__main-speaker") else "",
        f"Cycle : {txt(card, '.card-event__cycle')}" if txt(card, ".card-event__cycle") else "",
        f"Info : {txt(card, '.card-event__status')}" if txt(card, ".card-event__status") else "",
        f"Lien : {url}" if url else "",
    ]
    return {
        "title": title, "start": start, "end": end, "all_day": False,
        "place": txt(card, ".card-event__place"),
        "description": "\n".join(x for x in desc if x),
        "url": url,
    }


def container_range(card, url):
    """Plage de dates d'un colloque, quand on ne trouve pas son programme."""
    stamps = [t["datetime"] for t in card.select("time[datetime]") if not t["datetime"].endswith("Z")]
    if not stamps:
        return None
    start = parse_dt(stamps[0])
    end = parse_dt(stamps[1]) if len(stamps) > 1 else start + timedelta(hours=1)
    if end <= start:
        end = start + timedelta(hours=1)
    title = txt(card, ".card-event__title-decorator")
    return {"title": title, "start": start, "end": end, "all_day": False,
            "place": txt(card, ".card-event__place"),
            "description": f"Colloque\nLien : {url}", "url": url}


def college_de_france():
    events = {}
    containers = {}  # colloques : un par page, même s'ils apparaissent plusieurs jours
    for page in range(CDF_PAGES):
        for card in get_cards(f"{CDF_BASE}/fr/agenda?page={page}"):
            ev = make_event(card)
            if ev:
                events[(ev["title"], ev["start"])] = ev
                continue
            link = card.select_one("a.card-event__link")
            if link and link["href"] not in containers:
                containers[link["href"]] = card
    for href, card in containers.items():
        url = CDF_BASE + href
        parent = txt(card, ".card-event__title-decorator")
        sessions = []
        try:
            for sc in get_cards(url):
                ev = make_event(sc, parent=parent, default_url=url)
                if ev:
                    sessions.append(ev)
        except Exception as exc:
            print(f"ERREUR programme {url}: {exc}", file=sys.stderr)
        if not sessions:
            ev = container_range(card, url)
            sessions = [ev] if ev else []
        for ev in sessions:
            events[(ev["title"], ev["start"])] = ev
    return sorted(events.values(), key=lambda e: e["start"])


SOURCES = [college_de_france]


def esc(t):
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fmt(dt, all_day):
    if all_day:
        return dt.strftime("%Y%m%d")
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(events):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//calendrier-conferences//FR",
           "CALSCALE:GREGORIAN", "X-WR-CALNAME:Conférences", "X-WR-TIMEZONE:Europe/Paris",
           "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for e in events:
        uid = hashlib.md5((e["url"] + e["start"].isoformat()).encode()).hexdigest() + "@conferences"
        out += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{now}"]
        if e["all_day"]:
            out += [f"DTSTART;VALUE=DATE:{fmt(e['start'], True)}", f"DTEND;VALUE=DATE:{fmt(e['end'], True)}"]
        else:
            out += [f"DTSTART:{fmt(e['start'], False)}", f"DTEND:{fmt(e['end'], False)}"]
        out += [f"SUMMARY:{esc(e['title'])}", f"LOCATION:{esc(e['place'])}",
                f"DESCRIPTION:{esc(e['description'])}", f"URL:{e['url']}", "END:VEVENT"]
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


def main():
    events = []
    for source in SOURCES:
        try:
            events += source()
        except Exception as exc:  # une source en panne ne bloque pas les autres
            print(f"ERREUR source {source.__name__}: {exc}", file=sys.stderr)
    if KEYWORDS:
        kw = [k.lower() for k in KEYWORDS]
        events = [e for e in events if any(k in (e["title"] + e["description"]).lower() for k in kw)]
    if not events:
        sys.exit("Aucun événement trouvé : le fichier existant est conservé.")
    with open("conferences.ics", "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(events))
    print(f"{len(events)} événements écrits dans conferences.ics")


if __name__ == "__main__":
    main()
