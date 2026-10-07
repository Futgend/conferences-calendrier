"""Génère un fichier .ics par institution à partir des agendas de conférences.
Pour ajouter une institution : écrire une fonction qui renvoie la liste des
événements, puis l'ajouter au dictionnaire SOURCES.
"""
import hashlib
import json
import re
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


def get_cards_from(url, selector):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser").select(selector)


def txt(card, selector):
    el = card.select_one(selector)
    return " ".join(el.get_text().split()) if el else ""


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


ACAD_BASE = "https://www.academie-sciences.fr"
# Ce site refuse les visiteurs automatiques : on se présente comme un navigateur
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
}


def academie_des_sciences():
    session = requests.Session()
    session.headers.update(BROWSER_HEADERS)
    r = session.get(f"{ACAD_BASE}/events", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    events = []
    for card in soup.select(".NodeEventTeaser"):
        link = card.select_one(".NodeEventTeaser-title a")
        t = card.select_one("time[datetime]")
        if not link or not t:
            continue
        url = ACAD_BASE + link["href"]
        title = " ".join(link.get_text().split())
        start = parse_dt(t["datetime"])
        end = start + timedelta(hours=2)
        summary = ""
        try:  # la page de détail donne l'heure de fin et un résumé
            pr = session.get(url, timeout=30)
            pr.raise_for_status()
            page = BeautifulSoup(pr.text, "html.parser")
            times = page.select("time[datetime]")
            if len(times) > 1:
                candidate = parse_dt(times[1]["datetime"])
                if timedelta(0) < candidate - start <= timedelta(hours=12):
                    end = candidate
            meta = page.select_one('meta[name="description"]')
            if meta and meta.get("content"):
                summary = " ".join(meta["content"].split())
                if summary.startswith(title):
                    summary = summary[len(title):].strip()
        except Exception as exc:
            print(f"ERREUR détail {url}: {exc}", file=sys.stderr)
        desc = [
            f"Type : {txt(card, '.NodeEventTeaser-type')}" if txt(card, ".NodeEventTeaser-type") else "",
            f"Public : {txt(card, '.NodeEventTeaser-audience')}" if txt(card, ".NodeEventTeaser-audience") else "",
            f"Inscription : {txt(card, '.NodeEventTeaser-status')}" if txt(card, ".NodeEventTeaser-status") else "",
            summary,
            f"Lien : {url}",
        ]
        events.append({
            "title": title, "start": start, "end": end, "all_day": False,
            "place": txt(card, ".NodeEventTeaser-location"),
            "description": "\n".join(x for x in desc if x),
            "url": url,
        })
    return sorted(events, key=lambda e: e["start"])


def academie_depuis_fichier():
    """Le site bloque GitHub : on lit un fichier mis à jour à la main."""
    with open("academie-des-sciences.json", encoding="utf-8") as f:
        data = json.load(f)
    events = []
    for d in data:
        desc = [
            f"Type : {d['type']}" if d.get("type") else "",
            f"Public : {d['audience']}" if d.get("audience") else "",
            f"Inscription : {d['status']}" if d.get("status") else "",
            d.get("summary", ""),
            f"Lien : {d['url']}",
        ]
        events.append({
            "title": d["title"], "start": parse_dt(d["start"]), "end": parse_dt(d["end"]),
            "all_day": False, "place": d.get("place", ""),
            "description": "\n".join(x for x in desc if x), "url": d["url"],
        })
    return sorted(events, key=lambda e: e["start"])


def academie_avec_secours():
    try:
        return academie_des_sciences()
    except Exception as exc:
        print(f"Académie : site inaccessible ({exc}), lecture du fichier manuel.", file=sys.stderr)
        return academie_depuis_fichier()


IDC_BASE = "https://institutducerveau.org"
IDC_PAGES = 6  # 6 événements par page
# Catégories ignorées : événements destinés à un public scientifique (en minuscules)
IDC_EXCLUDE = {"événements scientifiques"}
MOIS = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
        "décembre": 12, "decembre": 12}
DATE_RE = re.compile(r"(\d{1,2})\s+([^\W\d_]+)\s+(\d{4})")
HEURE_RE = re.compile(r"(\d{1,2})\s*h\s*(\d{2})?")


def institut_du_cerveau():
    events = {}
    for page in range(IDC_PAGES):
        url = (f"{IDC_BASE}/agenda?field_date_start_value=&field_date_end_value="
               f"&field_event_category_value=All&page={page}")
        cards = get_cards_from(url, ".card-event")
        if not cards:
            break
        for card in cards:
            link = card.select_one("a.card-event-title")
            raw = txt(card, ".card-event-dates")
            if not link or not raw:
                continue
            if txt(card, ".card-event-category").lower() in IDC_EXCLUDE:
                continue  # événement destiné à un public scientifique
            text = raw.lower()
            dates = [(int(d), MOIS.get(m), int(y)) for d, m, y in DATE_RE.findall(text)]
            dates = [x for x in dates if x[1]]
            if not dates:
                continue
            heures = [(int(h), int(mn or 0)) for h, mn in HEURE_RE.findall(DATE_RE.sub("", text))]
            d0, d1 = dates[0], dates[-1]
            if heures:
                start = datetime(d0[2], d0[1], d0[0], heures[0][0], heures[0][1], tzinfo=PARIS)
                if len(heures) > 1:
                    end = datetime(d1[2], d1[1], d1[0], heures[1][0], heures[1][1], tzinfo=PARIS)
                else:
                    end = start + timedelta(hours=1)
                if end <= start:
                    end = start + timedelta(hours=1)
                all_day = False
            else:  # pas d'horaire : événement sur la journée (ou plusieurs jours)
                start = datetime(d0[2], d0[1], d0[0], tzinfo=PARIS)
                end = datetime(d1[2], d1[1], d1[0], tzinfo=PARIS) + timedelta(days=1)
                all_day = True
            href = IDC_BASE + link["href"]
            bottom = txt(card, ".card-event-bottom")
            place = txt(card, ".card-event-location")
            desc = [
                f"Catégorie : {txt(card, '.card-event-category')}" if txt(card, ".card-event-category") else "",
                "Aussi à distance" if "distance" in bottom.lower() else "",
                "Lieu : à l'extérieur de l'Institut (voir la page)" if "extérieur" in bottom.lower() else "",
                txt(card, ".card-event-text"),
                f"Lien : {href}",
            ]
            events[(href, start)] = {
                "title": txt(card, ".card-event-title"), "start": start, "end": end,
                "all_day": all_day, "place": place,
                "description": "\n".join(x for x in desc if x), "url": href,
            }
    return sorted(events.values(), key=lambda e: e["start"])


EHESS_BASE = "https://www.ehess.fr"
EHESS_LISTE = EHESS_BASE + "/jcms/kmo_28682/fr/agenda-de-l-ehess"
# Catégories ignorées : soutenances et vie étudiante (en minuscules)
EHESS_EXCLUDE = {"soutenance hdr", "vie étudiante"}
MOIS_ABR = [("janv", 1), ("fév", 2), ("fev", 2), ("mars", 3), ("avr", 4), ("mai", 5), ("juin", 6),
            ("juil", 7), ("aoû", 8), ("aou", 8), ("sept", 9), ("oct", 10), ("nov", 11),
            ("déc", 12), ("dec", 12)]
EHESS_DATE_RE = re.compile(r"(\d{1,2})\s+([^\W\d_]+\.?)\s+(\d{4})(?:\s+(\d{1,2})h(\d{2})?)?")
EHESS_HEURES_RE = re.compile(r"De\s+(\d{1,2})h(\d{2})?\s+à\s+(\d{1,2})h(\d{2})?")
EHESS_LIEU_RE = re.compile(r"\s(?:Le|Du)\s\d{1,2}/\d{1,2}/\d{4}")


def mois_abr(s):
    s = s.lower().strip(".")
    for prefixe, num in MOIS_ABR:
        if s.startswith(prefixe):
            return num
    return None


JOUR_RE = re.compile(r"^(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+(\d{1,2})(?:er)?\s+([^\W\d_]+)", re.I)
HEURE_PROG_RE = re.compile(r"^(\d{1,2})\s*h\s*(\d{2})?\s*(?:[-–—]\s*(\d{1,2})\s*h\s*(\d{2})?\s*)?[:\-–—]\s*(.*)")


def ehess_programme(page, debut, fin):
    """Horaires jour par jour d'un événement de plusieurs jours, lus dans son programme.
    Renvoie [(jour, (h1, m1, h2, m2, fin_estimee))]. La fin est estimée (dernière session + 1h30)
    quand le programme ne la donne pas."""
    article = page.select_one("article") or page
    jours, courant = {}, None
    for brut in article.get_text("\n").split("\n"):
        ligne = " ".join(brut.split())
        if not ligne:
            continue
        m = JOUR_RE.match(ligne)
        if m and len(ligne) < 40:
            courant = None
            mois = MOIS.get(m.group(2).lower())
            if mois:
                for annee in {debut.year, fin.year}:
                    try:
                        jour = datetime(annee, mois, int(m.group(1))).date()
                    except ValueError:
                        continue
                    if debut <= jour <= fin:
                        courant = jour
                        jours.setdefault(jour, [])
                        break
            continue
        h = HEURE_PROG_RE.match(ligne)
        if h and courant is not None:
            fin_prog = (int(h.group(3)), int(h.group(4) or 0)) if h.group(3) else None
            jours[courant].append((int(h.group(1)), int(h.group(2) or 0), fin_prog, h.group(5)))
    resultat = []
    for jour in sorted(jours):
        items = jours[jour]
        if not items:
            continue
        h1, m1 = items[0][0], items[0][1]
        dernier = items[-1]
        if dernier[2]:
            h2, m2, estime = dernier[2][0], dernier[2][1], False
        elif re.match(r"fin\b", dernier[3], re.I):
            h2, m2, estime = dernier[0], dernier[1], False
        else:
            total = min(dernier[0] * 60 + dernier[1] + 90, 23 * 60 + 59)
            h2, m2, estime = total // 60, total % 60, True
        resultat.append((jour, (h1, m1, h2, m2, estime)))
    return resultat


def ehess():
    r = requests.get(EHESS_LISTE, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    aujourdhui = datetime.now(PARIS).date()
    events = {}
    for card in soup.select("a.jnews-event-card"):
        href = card.get("href", "")
        titre = txt(card, ".jnews-event-title")
        bloc = card.select_one(".dates")
        if not href or not titre or not bloc:
            continue
        categories = [" ".join(c.get_text().split()) for c in card.select(".meta-cat")]
        if any(c.lower() in EHESS_EXCLUDE for c in categories):
            continue
        brut = " ".join(bloc.get_text(" ").split())
        dates = []
        for d, m, y, h, mn in EHESS_DATE_RE.findall(brut):
            if mois_abr(m):
                dates.append((int(y), mois_abr(m), int(d), int(h) if h else None, int(mn) if mn else 0))
        if not dates:
            continue
        d0, d1 = dates[0], dates[-1]
        if (d1[0], d1[1], d1[2]) < (d0[0], d0[1], d0[2]):
            d1 = d0  # date de fin incohérente sur le site : on garde le début seul
        jour0 = datetime(d0[0], d0[1], d0[2], tzinfo=PARIS)
        jour1 = datetime(d1[0], d1[1], d1[2], tzinfo=PARIS)
        multi = jour1.date() > jour0.date()
        fin_du_jour = jour1.date() if multi else jour0.date()
        if fin_du_jour < aujourdhui:
            continue  # événement passé
        url = f"{EHESS_BASE}/{href.lstrip('/')}"
        # page de détail : lieu exact, horaires, accès, résumé
        place, heures, acces, resume = "", None, "", ""
        jours_prog = []
        try:
            pr = requests.get(url, headers=HEADERS, timeout=30)
            pr.raise_for_status()
            page = BeautifulSoup(pr.text, "html.parser")
            infos = [" ".join(e.get_text(" ").split()) for e in page.select(".infos-container .line-infos")]
            ligne = infos[0] if infos else ""
            acces = infos[1] if len(infos) > 1 else ""
            m = EHESS_LIEU_RE.search(ligne)
            place = ligne[:m.start()].strip() if m else ""
            hm = EHESS_HEURES_RE.search(ligne)
            if hm:
                heures = (int(hm.group(1)), int(hm.group(2) or 0), int(hm.group(3)), int(hm.group(4) or 0))
            meta = page.select_one('meta[name="description"]')
            if meta and meta.get("content"):
                resume = " ".join(meta["content"].split())[:300]
            jours_prog = ehess_programme(page, jour0.date(), jour1.date())
        except Exception as exc:
            print(f"ERREUR détail {url}: {exc}", file=sys.stderr)
        if not place:
            ville = card.select_one(".caption .title p.subtitle")
            place = " ".join(ville.get_text(" ").split()) if ville else ""
        note = ""
        if multi and jours_prog and len(jours_prog) == (jour1.date() - jour0.date()).days + 1:
            for jour, (h1, m1, h2, m2, estime) in jours_prog:  # un événement par jour, aux horaires du programme
                debut = datetime(jour.year, jour.month, jour.day, h1, m1, tzinfo=PARIS)
                fin = datetime(jour.year, jour.month, jour.day, h2, m2, tzinfo=PARIS)
                if fin <= debut:
                    fin = debut + timedelta(hours=2)
                lignes = [
                    f"Catégorie : {', '.join(categories)}" if categories else "",
                    f"Accès : {acces}" if acces else "",
                    "Horaires d'après le programme" + (" (heure de fin estimée)" if estime else ""),
                    resume,
                    f"Lien : {url}",
                ]
                events[(url, debut)] = {
                    "title": titre, "start": debut, "end": fin, "all_day": False, "place": place,
                    "description": "\n".join(x for x in lignes if x), "url": url,
                }
            continue
        if multi:  # plusieurs jours : événement sur la durée, horaires dans la description
            start, end, all_day = jour0, jour1 + timedelta(days=1), True
            if d0[3] is not None:
                note = f"Du {d0[2]:02d}/{d0[1]:02d} à {d0[3]}h{d0[4]:02d} au {d1[2]:02d}/{d1[1]:02d}" + (f" à {d1[3]}h{d1[4]:02d}" if d1[3] is not None else "")
        elif heures:
            start = jour0.replace(hour=heures[0], minute=heures[1])
            end = jour0.replace(hour=heures[2], minute=heures[3])
            all_day = False
        elif d0[3] is not None and (d0[3], d0[4]) != (0, 0):
            start = jour0.replace(hour=d0[3], minute=d0[4])
            end = start + timedelta(hours=2)
            all_day = False
        else:
            start, end, all_day = jour0, jour0 + timedelta(days=1), True
        if not all_day and end <= start:
            end = start + timedelta(hours=2)
        desc = [
            f"Catégorie : {', '.join(categories)}" if categories else "",
            f"Accès : {acces}" if acces else "",
            note,
            resume,
            f"Lien : {url}",
        ]
        events[(url, start)] = {
            "title": titre, "start": start, "end": end, "all_day": all_day, "place": place,
            "description": "\n".join(x for x in desc if x), "url": url,
        }
    return sorted(events.values(), key=lambda e: e["start"])


IHPST_BASE = "https://ihpst.pantheonsorbonne.fr"
IHPST_PAGES = 6  # 12 événements par page
# Titres ignorés : formations internes (en minuscules)
IHPST_EXCLUDE = ("formation interne",)
IHPST_LIEU_RE = re.compile(r"^.*?\d{2}/\d{2}/\d{4}\s*-\s*\d{1,2}:\d{2}\s*")


def ihpst():
    events = {}
    for page in range(IHPST_PAGES):
        cards = get_cards_from(f"{IHPST_BASE}/evenements?page={page}", "article.event")
        if not cards:
            break
        for card in cards:
            lien = card.select_one("a[href]")
            titre = txt(card, ".title")
            jour = txt(card, ".date-day-entry").lower()
            if not lien or not titre or not jour:
                continue
            if any(x in titre.lower() for x in IHPST_EXCLUDE):
                continue
            d = [(int(a), MOIS.get(b), int(c)) for a, b, c in DATE_RE.findall(jour)]
            d = [x for x in d if x[1]]
            if not d:
                continue
            j, mo, an = d[0]
            bloc = card.select_one(".date-hours-wrapper")
            heures = HEURE_RE.findall(bloc.get_text(" ")) if bloc else []
            if heures:
                start = datetime(an, mo, j, int(heures[0][0]), int(heures[0][1] or 0), tzinfo=PARIS)
                if len(heures) > 1:
                    end = datetime(an, mo, j, int(heures[1][0]), int(heures[1][1] or 0), tzinfo=PARIS)
                else:
                    end = start + timedelta(hours=1, minutes=30)
                if end <= start:
                    end = start + timedelta(hours=1, minutes=30)
                all_day = False
            else:
                start = datetime(an, mo, j, tzinfo=PARIS)
                end = start + timedelta(days=1)
                all_day = True
            href = lien["href"]
            url = href if href.startswith("http") else IHPST_BASE + href
            place, resume = "", ""
            try:  # page de détail : lieu exact et résumé
                pr = requests.get(url, headers=HEADERS, timeout=30)
                pr.raise_for_status()
                detail = BeautifulSoup(pr.text, "html.parser")
                lieu = txt(detail, ".location-content")
                place = IHPST_LIEU_RE.sub("", lieu).strip() if lieu else ""
                resume = txt(detail, ".field--name-bp-text")[:400]
            except Exception as exc:
                print(f"ERREUR détail {url}: {exc}", file=sys.stderr)
            if not place:  # secours : le fichier calendrier du site donne le lieu
                ics = card.select_one('a[href*="icsfiles"]')
                if ics:
                    try:
                        ir = requests.get(IHPST_BASE + ics["href"], headers=HEADERS, timeout=30)
                        m = re.search(r"DESCRIPTION[^:\r\n]*:([^\r\n]*)", ir.content.decode("utf-8", "replace"))
                        if m:
                            place = m.group(1).replace("\\,", ",").replace("\\n", " ").strip()
                    except Exception:
                        pass
            note = "Lieu à venir" if place.lower() in ("à venir", "a venir") else ""
            if note:
                place = ""
            desc = [
                f"Catégorie : {txt(card, '.categ-style')}" if txt(card, ".categ-style") else "",
                note,
                resume,
                f"Lien : {url}",
            ]
            events[(url, start)] = {
                "title": titre, "start": start, "end": end, "all_day": all_day, "place": place,
                "description": "\n".join(x for x in desc if x), "url": url,
            }
    return sorted(events.values(), key=lambda e: e["start"])


# Une entrée par institution : nom du fichier -> (nom du calendrier, fonction)
SOURCES = {
    "college-de-france": ("Conférences Collège de France", college_de_france),
    "academie-des-sciences": ("Conférences Académie des sciences", academie_avec_secours),
    "institut-du-cerveau": ("Conférences Institut du Cerveau", institut_du_cerveau),
    "ehess": ("Conférences EHESS", ehess),
    "ihpst": ("Conférences IHPST", ihpst),
}
# Ancien nom de fichier, conservé le temps de changer d'abonnement
LEGACY = {"college-de-france": "conferences"}


def esc(t):
    return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fmt(dt, all_day):
    if all_day:
        return dt.strftime("%Y%m%d")
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(events, name):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//calendrier-conferences//FR",
           "CALSCALE:GREGORIAN", f"X-WR-CALNAME:{name}", "X-WR-TIMEZONE:Europe/Paris",
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
    ok = 0
    for key, (cal_name, fetch) in SOURCES.items():
        try:
            events = fetch()
        except Exception as exc:  # une source en panne ne bloque pas les autres
            print(f"ERREUR {key}: {exc}", file=sys.stderr)
            continue
        if KEYWORDS:
            kw = [k.lower() for k in KEYWORDS]
            events = [e for e in events if any(k in (e["title"] + e["description"]).lower() for k in kw)]
        if not events:
            print(f"{key}: aucun événement trouvé, fichier existant conservé.", file=sys.stderr)
            continue
        ics = build_ics(events, cal_name)
        for filename in [key] + ([LEGACY[key]] if key in LEGACY else []):
            with open(f"{filename}.ics", "w", encoding="utf-8", newline="") as f:
                f.write(ics)
        print(f"{key}: {len(events)} événements écrits")
        ok += 1
    if ok == 0:
        sys.exit("Aucune source n'a fonctionné.")


if __name__ == "__main__":
    main()
