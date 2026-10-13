#!/usr/bin/env python3
"""Mise à jour quotidienne des données du suivi des taux courts de Stealth AM.

Sources :
  - Bundesbank, courbe des titres fédéraux (Svensson), 1 et 2 ans : API statistique publique
  - Banque de France (Webstat), taux de l'échéance constante (TEC) 1 et 2 ans : API, clé dans WEBSTAT_API_KEY
  - Bundesbank, prix de référence par ISIN : fichier Excel mensuel
  - Borsa Italiana, liste des BOT sur le MOT : page HTML

Principe de prudence : chaque source est traitée séparément. Si une source échoue ou renvoie
des données incohérentes, les données existantes ne sont pas modifiées et l'échec est noté
dans data/status.json. Le script ne supprime jamais de données.

Usage : python scripts/update_data.py [--dry-run]
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PARIS = ZoneInfo("Europe/Paris")
UA = {"User-Agent": "StealthAM-suivi-taux/1.0 (+https://stealtham.vercel.app)"}
TIMEOUT = 40

BBK_KEYS = {
    "1Y": "D.I.ZST.ZI.EUR.S1311.B.A604.R01XX.R.A.A._Z._Z.A",
    "2Y": "D.I.ZST.ZI.EUR.S1311.B.A604.R02XX.R.A.A._Z._Z.A",
}
TEC_KEYS = {
    "1Y": "FM.D.FR.EUR.FR2.BB.FRMOYTEC1.HSTA",
    "2Y": "FM.D.FR.EUR.FR2.BB.FRMOYTEC2.HSTA",
}
HISTORY_START = "2026-04-01"
MONTHS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


# ---------------------------------------------------------------- utilitaires
def load(name: str):
    p = DATA / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(name: str, obj) -> None:
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def today_paris() -> dt.date:
    return dt.datetime.now(PARIS).date()


def get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def merge_series(old: list, new: list) -> list:
    """Fusionne deux listes [[date, valeur], ...] sans doublon, triées par date."""
    d = {a: b for a, b in old}
    d.update({a: b for a, b in new})
    return [[k, d[k]] for k in sorted(d)]


def fr_num(s: str) -> float | None:
    s = s.strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(s: str) -> str | None:
    s = s.strip()
    for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------- Bundesbank Svensson
def parse_sdmx(text: str) -> list:
    """Extrait [date, valeur] d'une réponse SDMX (XML générique, XML spécifique ou CSV)."""
    out = []
    if "<" in text[:200]:
        # XML générique : <generic:Obs><generic:ObsDimension value="2026-10-12"/><generic:ObsValue value="2.94"/>
        for block in re.findall(r"<(?:\w+:)?Obs\b(.*?)</(?:\w+:)?Obs>", text, flags=re.S):
            d = re.search(r'ObsDimension[^>]*value="([^"]+)"', block)
            v = re.search(r'ObsValue[^>]*value="([^"]+)"', block)
            if d and v:
                out.append((d.group(1), v.group(1)))
        # XML spécifique : <Obs TIME_PERIOD="2026-10-12" OBS_VALUE="2.94" .../>
        if not out:
            for attrs in re.findall(r"<(?:\w+:)?Obs\b([^>]*)/?>", text):
                d = re.search(r'TIME_PERIOD="([^"]+)"', attrs)
                v = re.search(r'OBS_VALUE="([^"]+)"', attrs)
                if d and v:
                    out.append((d.group(1), v.group(1)))
    else:
        lines = [l for l in text.splitlines() if l.strip()]
        if lines:
            sep = ";" if lines[0].count(";") > lines[0].count(",") else ","
            head = [h.strip().strip('"').upper() for h in lines[0].split(sep)]
            if "TIME_PERIOD" in head and "OBS_VALUE" in head:
                it, iv = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
                for l in lines[1:]:
                    c = [x.strip().strip('"') for x in l.split(sep)]
                    if len(c) > max(it, iv):
                        out.append((c[it], c[iv]))
    res = []
    for d, v in out:
        x = fr_num(v)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and x is not None:
            res.append([d, round(x, 4)])
    return sorted(res)


def update_bbk(series: dict, log: dict) -> None:
    doc = series.setdefault("DE-svensson", {"source": "Bundesbank, courbe des taux des titres fédéraux (Svensson), valeurs quotidiennes", "tenors": {}})
    for tenor, key in BBK_KEYS.items():
        cur = doc["tenors"].get(tenor, [])
        start = HISTORY_START if not cur or cur[0][0] > HISTORY_START else cur[-1][0]
        url = f"https://api.statistiken.bundesbank.de/rest/data/BBSIS/{key}?startPeriod={start}"
        try:
            obs = parse_sdmx(get(url, headers={"Accept": "application/vnd.sdmx.data+csv;version=1.0.0, application/xml;q=0.9"}).text)
            obs = [o for o in obs if o[0] >= start and 0 < abs(o[1]) < 20]
            if not obs:
                raise ValueError("aucune observation exploitable")
            doc["tenors"][tenor] = merge_series(cur, obs)
            log[f"bbk_{tenor}"] = f"ok, dernière valeur {obs[-1][0]}"
        except Exception as e:  # noqa: BLE001
            log[f"bbk_{tenor}"] = f"échec : {e}"
    if doc["tenors"]:
        doc["updated"] = max(v[-1][0] for v in doc["tenors"].values() if v)


# ---------------------------------------------------------------- Banque de France TEC
def update_tec(series: dict, log: dict) -> None:
    key = os.environ.get("WEBSTAT_API_KEY", "").strip()
    if not key:
        log["tec"] = "ignoré : WEBSTAT_API_KEY absente"
        return
    doc = series.setdefault("FR-tec", {"source": "Banque de France (Webstat), taux de l'échéance constante (TEC)", "tenors": {}})
    base = "https://webstat.banque-france.fr/api/explore/v2.1/catalog/datasets/observations/exports/json"
    for tenor, sk in TEC_KEYS.items():
        cur = doc["tenors"].get(tenor, [])
        start = HISTORY_START if not cur else cur[-1][0]
        params = {
            "refine": f"series_key:{sk}",
            "where": f"time_period_start >= date'{start}'",
            "select": "series_key,time_period_start,obs_value",
            "limit": -1,
        }
        try:
            rows = get(base, params=params, headers={"Authorization": f"Apikey {key}"}).json()
            obs = []
            for r in rows:
                if r.get("series_key") not in (None, sk):
                    continue
                d, v = str(r.get("time_period_start", ""))[:10], r.get("obs_value")
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and isinstance(v, (int, float)) and 0 < abs(v) < 20:
                    obs.append([d, round(float(v), 4)])
            if not obs:
                raise ValueError("aucune observation exploitable")
            doc["tenors"][tenor] = merge_series(cur, sorted(obs))
            log[f"tec_{tenor}"] = f"ok, dernière valeur {sorted(obs)[-1][0]}"
        except Exception as e:  # noqa: BLE001
            log[f"tec_{tenor}"] = f"échec : {e}"
    if doc["tenors"]:
        doc["updated"] = max(v[-1][0] for v in doc["tenors"].values() if v)


# ---------------------------------------------------------------- Bundesbank ISIN (Excel mensuel)
def fr_label(desc: str) -> str:
    """'BKO 2.2 03/11/27' -> 'BKO 2,2 11/03/27' ; ajoute 'vert' aux titres verts."""
    m = re.match(r"\s*([A-Z]{3})\s+([\d.]+)\s+(\d{2})/(\d{2})/(\d{2})(.*)", desc)
    if not m:
        return desc.strip()
    kind, cpn, mm, dd, yy, rest = m.groups()
    green = " vert" if re.search(r"\bG\b", rest) else ""
    return f"{kind} {cpn.replace('.', ',')} {dd}/{mm}/{yy}{green}"


def parse_bbk_xlsx(content: bytes) -> tuple[str, list] | None:
    """Repère le dernier bloc daté du fichier et renvoie (date, lignes)."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    blocks: dict[str, list] = {}
    for ws in wb.worksheets:
        cur_date = None
        m = re.search(r"(\d{2}\.\d{2}\.\d{2,4})", ws.title or "")
        if m:
            cur_date = parse_date(m.group(1))
        for row in ws.iter_rows(values_only=True):
            cells = ["" if c is None else c for c in row]
            text = " ".join(str(c) for c in cells)
            m = re.search(r"as of\s+(\d{2}\.\d{2}\.\d{2,4})", text, flags=re.I)
            if m:
                cur_date = parse_date(m.group(1))
                continue
            isin = None
            for c in cells:
                s = re.sub(r"\s", "", str(c))
                if re.fullmatch(r"DE[0-9A-Z]{10}", s):
                    isin = s
                    break
            if not isin or not cur_date:
                continue
            mat, nums, desc = None, [], ""
            for c in cells:
                if isinstance(c, (dt.datetime, dt.date)):
                    mat = (c.date() if isinstance(c, dt.datetime) else c).isoformat()
                elif isinstance(c, (int, float)):
                    nums.append(float(c))
                elif isinstance(c, str):
                    d = parse_date(c)
                    if d and not mat:
                        mat = d
                    elif re.match(r"\s*[A-Z]{3}\s+[\d.]", c) and not desc:
                        desc = c
            prices = [x for x in nums if 80 <= x <= 130]
            ylds = [x for x in nums if -2 <= x <= 12]
            if mat and prices and ylds:
                blocks.setdefault(cur_date, []).append(
                    {"isin": isin, "label": fr_label(desc) if desc else isin, "maturity": mat, "price": prices[0], "yield": ylds[-1]}
                )
    if not blocks:
        return None
    last = max(blocks)
    return last, blocks[last]


def update_bbk_isin(snaps: dict, log: dict) -> None:
    try:
        page = get("https://www.bundesbank.de/en/service/federal-securities/prices-and-yields").text
        links = re.findall(r'href="([^"]*?(\d{4}-\d{2})-excel-data\.xlsx)"', page)
        if not links:
            raise ValueError("lien vers le fichier Excel introuvable")
        href = sorted(links, key=lambda x: x[1])[-1][0]
        url = href if href.startswith("http") else "https://www.bundesbank.de" + href
        parsed = parse_bbk_xlsx(get(url).content)
        if not parsed:
            raise ValueError("aucun tableau lisible dans le fichier")
        date, lines = parsed
        horizon = (dt.date.fromisoformat(date) + dt.timedelta(days=460)).isoformat()
        lines = [l for l in lines if date < l["maturity"] <= horizon]
        if len(lines) < 4:
            raise ValueError(f"trop peu de lignes courtes ({len(lines)})")
        key = f"DE-{date}"
        if key in snaps:
            log["bbk_isin"] = f"inchangé, dernier tableau au {date}"
            return
        snaps[key] = {"country": "DE", "date": date, "kind": "isin", "source": "Bundesbank, prix de référence", "lines": lines}
        log["bbk_isin"] = f"ok, {len(lines)} lignes au {date}"
    except Exception as e:  # noqa: BLE001
        log["bbk_isin"] = f"échec : {e}"


# ---------------------------------------------------------------- Borsa Italiana BOT
def bot_label(desc: str, mat: str) -> str:
    y, m, _ = mat.split("-")
    semi = " (S)" if re.search(r"\bS\b", desc) else ""
    return f"BOT {MONTHS_FR[int(m) - 1]} {y}{semi}"


def update_bot(snaps: dict, log: dict) -> None:
    try:
        html = get("https://www.borsaitaliana.it/borsa/obbligazioni/mot/bot/lista.html").text
        lines = []
        for tr in re.findall(r"<tr\b.*?</tr>", html, flags=re.S | re.I):
            cells = [re.sub(r"<[^>]+>", " ", c) for c in re.findall(r"<t[dh]\b.*?</t[dh]>", tr, flags=re.S | re.I)]
            cells = [re.sub(r"\s+", " ", c).strip() for c in cells]
            joined = " ".join(cells)
            m = re.search(r"\b(IT\d{10})\b", joined) or re.search(r"(IT\d{10})", tr)
            if not m:
                continue
            isin = m.group(1)
            mat = next((parse_date(x) for x in re.findall(r"\d{2}/\d{2}/\d{2,4}", joined) if parse_date(x)), None)
            nums = [fr_num(x) for x in re.findall(r"\b\d{2,3},\d{2,3}\b", joined)]
            prices = [x for x in nums if x and 90 <= x <= 100.5]
            desc = next((c for c in cells if c.lower().startswith("bot")), "")
            if mat and prices:
                lines.append({"isin": isin, "label": bot_label(desc, mat), "maturity": mat, "price": prices[-1]})
        uniq = {l["isin"]: l for l in lines}
        lines = sorted(uniq.values(), key=lambda l: l["maturity"])
        if len(lines) < 5:
            raise ValueError(f"trop peu de BOT lus ({len(lines)})")
        date = today_paris().isoformat()
        prev = sorted(k for k in snaps if k.startswith("IT-"))
        if prev:
            old = {l["isin"]: l["price"] for l in snaps[prev[-1]]["lines"]}
            if old == {l["isin"]: l["price"] for l in lines}:
                log["bot"] = "inchangé : cours identiques au dernier relevé (jour férié ou données figées)"
                return
        snaps[f"IT-{date}"] = {"country": "IT", "date": date, "kind": "isin", "source": "Borsa Italiana (MOT), dernier cours", "note": "relevé après la clôture de 17 h 30", "lines": lines}
        log["bot"] = f"ok, {len(lines)} BOT au {date}"
    except Exception as e:  # noqa: BLE001
        log["bot"] = f"échec : {e}"


# ---------------------------------------------------------------- calendrier des émissions
def easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    return dt.date(y, mo, (h + l - 7 * m + 114) % 31 + 1)


def fr_holidays(y: int) -> set:
    e = easter(y)
    fixed = [(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)]
    return {dt.date(y, m, d) for m, d in fixed} | {e + dt.timedelta(days=1), e + dt.timedelta(days=39), e + dt.timedelta(days=50)}


def nth_weekday(y: int, m: int, wd: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def fr_fmt(d: dt.date) -> str:
    return d.strftime("%d/%m")


def next_business(d: dt.date, hol: set) -> dt.date:
    while d.weekday() >= 5 or d in hol:
        d += dt.timedelta(days=1)
    return d


def build_fr_calendar(start: dt.date, end: dt.date) -> list:
    """Calendrier indicatif de l'AFT : BTF chaque lundi, OAT long terme le 1er jeudi, moyen terme et indexées le 3e jeudi."""
    hol = fr_holidays(start.year) | fr_holidays(end.year)
    ev = []
    d = start + dt.timedelta(days=(0 - start.weekday()) % 7)
    while d <= end:
        a = next_business(d, hol)
        s = next_business(a + dt.timedelta(days=2), hol)
        ev.append({"date": a.isoformat(), "instrument": "BTF", "instrument_en": "BTF",
                   "detail": f"adjudication hebdomadaire, lignes et montants annoncés le jeudi précédent ; règlement le {fr_fmt(s)}",
                   "detail_en": f"weekly auction, lines and amounts announced the previous Thursday; settlement on {fr_fmt(s)}",
                   "short": True, "indicative": True})
        d += dt.timedelta(weeks=1)
    y, m = start.year, start.month
    while dt.date(y, m, 1) <= end:
        lt = nth_weekday(y, m, 3, 1)
        if m != 8:
            st = next_business(lt + dt.timedelta(days=4), hol)
            ev.append({"date": lt.isoformat(), "instrument": "OAT long terme", "instrument_en": "Long-term OATs",
                       "detail": f"plus de 8,5 ans ; règlement le {fr_fmt(st)}", "detail_en": f"over 8.5 years; settlement on {fr_fmt(st)}",
                       "short": False, "indicative": True})
        if m not in (8, 12):
            mt = nth_weekday(y, m, 3, 3)
            st = next_business(mt + dt.timedelta(days=4), hol)
            ev.append({"date": mt.isoformat(), "instrument": "OAT moyen terme et indexées", "instrument_en": "Medium-term and inflation-linked OATs",
                       "detail": f"2 à 8,5 ans, OATi et OAT€i ; règlement le {fr_fmt(st)}", "detail_en": f"2 to 8.5 years, OATi and OAT€i; settlement on {fr_fmt(st)}",
                       "short": False, "indicative": True})
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return [e for e in ev if start.isoformat() <= e["date"] <= end.isoformat()]


DE_INSTR = {"bubill": ("Bubill", "Bubill", True), "schatz": ("Schatz", "Schatz", False), "bobl": ("Bobl", "Bobl", False),
            "bund": ("Bund", "Bund", False)}


def parse_finanzagentur(html: str) -> list:
    ev = []
    for tr in re.findall(r"<tr\b.*?</tr>", html, flags=re.S | re.I):
        txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tr)).strip()
        dates = re.findall(r"\b(\d{2}\.\d{2}\.\d{4})\b", txt)
        m_ins = re.search(r"\b(Bubill|Schatz|Bobl|Bund|Green|Inflation[- ]linked)\b(?:\s*(\d{2})\s*Y)?", txt, flags=re.I)
        if not dates or not m_ins:
            continue
        date = parse_date(dates[0])
        kind = m_ins.group(1).lower()
        name, name_en, short = DE_INSTR.get(kind, (m_ins.group(1), m_ins.group(1), False))
        if m_ins.group(2):
            name, name_en = f"Bund {int(m_ins.group(2))} ans", f"{int(m_ins.group(2))}-year Bund"
        isin = re.search(r"\b(DE[0-9A-Z]{10})\b", txt)
        new = re.search(r"\(\s*N\b|new issue", txt, flags=re.I) is not None
        mat = parse_date(dates[1]) if len(dates) > 1 else None
        vol = re.search(r"(\d+[.,]\d+)\s*(?:€|EUR)\s*bn", txt, flags=re.I) or re.search(r"(\d+[.,]\d+)\s*bn", txt, flags=re.I)
        cpn = re.search(r"(\d+[.,]\d+)\s*%", txt)
        det = ("nouvelle ligne" if new else "réouverture") + (f", coupon {cpn.group(1).replace('.', ',')} %" if cpn else "") + (f", échéance {fmt_fr_date(mat)}" if mat else ", ligne précisée ultérieurement")
        det_en = ("new line" if new else "reopening") + (f", {cpn.group(1)}% coupon" if cpn else "") + (f", maturity {fmt_fr_date(mat)}" if mat else ", line to be specified")
        e = {"date": date, "instrument": name, "instrument_en": name_en, "detail": det, "detail_en": det_en, "short": short}
        if isin:
            e["isin"] = isin.group(1)
        if vol:
            e["volume"] = vol.group(1).replace(".", ",") + " Md€"
        if date:
            ev.append(e)
    uniq = {(e["date"], e["instrument"], e.get("isin", "")): e for e in ev}
    return sorted(uniq.values(), key=lambda e: e["date"])


def fmt_fr_date(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}/{y}"


def update_calendar(cal: dict, log: dict) -> None:
    today = today_paris()
    t, end = today.isoformat(), (today + dt.timedelta(days=100)).isoformat()
    # 1. retirer les adjudications passées, pour tous les pays
    for doc in cal.values():
        doc["events"] = [e for e in doc.get("events", []) if e.get("date", "") >= t]
    # 2. Allemagne : calendrier de la Finanzagentur
    try:
        html = get("https://www.deutsche-finanzagentur.de/en/federal-securities/issuances/issuance-calendar").text
        ev = [e for e in parse_finanzagentur(html) if t <= e["date"] <= end]
        if len(ev) < 3:
            raise ValueError(f"trop peu d'adjudications lues ({len(ev)})")
        doc = cal.setdefault("DE", {"country": "DE", "source": "Finanzagentur, calendrier d'émission", "source_en": "Finanzagentur, issuance calendar",
                                    "url": "https://www.deutsche-finanzagentur.de/en/federal-securities/issuances/issuance-calendar"})
        doc["events"], doc["updated"] = ev, t
        log["cal_DE"] = f"ok, {len(ev)} adjudications"
    except Exception as e:  # noqa: BLE001
        log["cal_DE"] = f"échec : {e}"
    # 3. France : calendrier indicatif de l'AFT, recalculé chaque jour
    doc = cal.setdefault("FR", {"country": "FR", "url": "https://www.aft.gouv.fr"})
    # les adjudications confirmées (indicative = false, renseignées par la tâche hebdomadaire) priment sur les dates calculées
    confirmed = [e for e in doc.get("events", []) if e.get("indicative") is False]
    taken = {(e["date"], e.get("short")) for e in confirmed}
    gen = [e for e in build_fr_calendar(today, dt.date.fromisoformat(end)) if (e["date"], e["short"]) not in taken]
    doc["events"] = sorted(confirmed + gen, key=lambda e: (e["date"], e["instrument"]))
    doc.update({"updated": t, "source": "AFT, règles du calendrier indicatif", "source_en": "AFT, indicative calendar rules",
                "note": "Dates calculées selon le rythme publié par l'AFT (BTF le lundi, OAT long terme le 1er jeudi, moyen terme et indexées le 3e jeudi) ; à confirmer par les annonces de l'AFT.",
                "note_en": "Dates computed from the AFT's published pattern (BTF on Mondays, long-term OATs on the 1st Thursday, medium-term and inflation-linked on the 3rd Thursday); to be confirmed by AFT announcements."})
    log["cal_FR"] = f"ok, {len(doc['events'])} adjudications indicatives"


# ---------------------------------------------------------------- principal
def summarize(log: dict) -> tuple[str, str]:
    ok = [k for k, v in log.items() if v.startswith("ok")]
    ko = [k for k, v in log.items() if v.startswith("échec")]
    names = {"bbk_1Y": "courbe DE 1 an", "bbk_2Y": "courbe DE 2 ans", "tec_1Y": "TEC FR 1 an", "tec_2Y": "TEC FR 2 ans", "bbk_isin": "prix DE par ISIN", "bot": "BOT italiens", "cal_DE": "calendrier DE", "cal_FR": "calendrier FR"}
    names_en = {"bbk_1Y": "DE curve 1y", "bbk_2Y": "DE curve 2y", "tec_1Y": "FR TEC 1y", "tec_2Y": "FR TEC 2y", "bbk_isin": "DE ISIN prices", "bot": "Italian BOT", "cal_DE": "DE calendar", "cal_FR": "FR calendar"}
    fr = ("mis à jour : " + ", ".join(names.get(k, k) for k in ok) if ok else "aucune donnée nouvelle") + (" ; échec : " + ", ".join(names.get(k, k) for k in ko) if ko else "")
    en = ("updated: " + ", ".join(names_en.get(k, k) for k in ok) if ok else "no new data") + ("; failed: " + ", ".join(names_en.get(k, k) for k in ko) if ko else "")
    return fr, en


def main() -> int:
    dry = "--dry-run" in sys.argv
    series, snaps, cal = load("series.json"), load("snapshots.json"), load("calendar.json")
    log: dict[str, str] = {}
    update_bbk(series, log)
    update_tec(series, log)
    update_bbk_isin(snaps, log)
    update_bot(snaps, log)
    update_calendar(cal, log)
    fr, en = summarize(log)
    status = {"lastRun": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "note": fr, "note_en": en, "sources": log}
    for k, v in log.items():
        print(f"{k:10s} {v}")
    if dry:
        print("(essai : rien n'est écrit)")
        return 0
    save("series.json", series)
    save("snapshots.json", snaps)
    save("calendar.json", cal)
    save("status.json", status)
    # code de sortie 0 même en cas d'échec partiel : les échecs sont visibles dans status.json
    return 0


if __name__ == "__main__":
    sys.exit(main())
