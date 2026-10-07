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
CDF_PAGES = 4  # nombre de pages d'agenda à lire (30 événements par page)


def parse_dt(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(PARIS)


def college_de_france():
    events = []
    for page in range(CDF_PAGES):
        r = requests.get(f"{CDF_BASE}/fr/agenda?page={page}", headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for card in soup.select(".card-event"):
            link = card.select_one("a.card-event__link")
            title = card.select_one(".card-event__title-decorator")
            if not link or not title:
                continue
            speaker = card.select_one(".card-event__main-speaker")
            cycle = card.select_one(".card-event__cycle")
            place = card.select_one(".card-event__place")
            kind = card.select_one(".card-event__type")
            status = card.select_one(".card-event__status")

            times = card.select(".card-event__time--desktop time[datetime]")
            start = end = None
            all_day = False
            if times:
                start = parse_dt(times[0]["datetime"])
                end = parse_dt(times[1]["datetime"]) if len(times) > 1 else start + timedelta(hours=1)
            else:  # colloque sur plusieurs jours, pas d'horaire
                d = card.select_one("time.smart-date__start[datetime]")
                if not d:
                    continue
                start = parse_dt(d["datetime"]).replace(hour=0, minute=0)
                end = start + timedelta(days=1)
                all_day = True

            desc = [
                f"Type : {kind.get_text(strip=True)}" if kind else "",
                f"Intervenant : {speaker.get_text(strip=True)}" if speaker else "",
                f"Cycle : {cycle.get_text(strip=True)}" if cycle else "",
                f"Info : {status.get_text(strip=True)}" if status else "",
                f"Lien : {CDF_BASE}{link['href']}",
            ]
            events.append({
                "source": "Collège de France",
                "title": title.get_text(strip=True),
                "start": start, "end": end, "all_day": all_day,
                "place": place.get_text(strip=True) if place else "",
                "description": "\n".join(x for x in desc if x),
                "url": CDF_BASE + link["href"],
            })
    return events


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
