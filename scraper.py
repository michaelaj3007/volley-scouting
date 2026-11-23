import html
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,cs;q=0.8",
}

ROLE_MAP = {
    "setter": "Setter",
    "opposite": "Opposite",
    "universal": "Opposite",
    "outside": "Outside Hitter",
    "wing": "Outside Hitter",
    "spiker": "Outside Hitter",
    "receiver": "Outside Hitter",
    "middle": "Middle Blocker",
    "blocker": "Middle Blocker",
    "libero": "Libero",
    "πασαδόρος": "Setter",
    "διαγώνιος": "Opposite",
    "ακραίος": "Outside Hitter",
    "κεντρικός": "Middle Blocker",
    "λίμπερο": "Libero",
    "ridicator": "Setter",
    "ridicător": "Setter",
    "fals": "Opposite",
    "extremă": "Outside Hitter",
    "extrema": "Outside Hitter",
    "secund": "Outside Hitter",
    "centru": "Middle Blocker",
    "palleggiatore": "Setter",
    "opposto": "Opposite",
    "schiacciatore": "Outside Hitter",
    "centrale": "Middle Blocker",
    "passare": "Setter",
    "högerspiker": "Opposite",
    "vänsterspiker": "Outside Hitter",
    "center": "Middle Blocker",
    "feladó": "Setter",
    "átló": "Opposite",
    "négyesütő": "Outside Hitter",
    "szélső": "Outside Hitter",
    "liberó": "Libero",
    "dizač": "Setter",
    "korektor": "Opposite",
    "primač": "Outside Hitter",
    "srednji": "Middle Blocker",
    "разпределител": "Setter",
    "диагонал": "Opposite",
    "посрещач": "Outside Hitter",
    "център": "Middle Blocker",
    "либеро": "Libero",
    "passari": "Setter",
    "hakkuri": "Opposite",
    "yleispelaaja": "Outside Hitter",
    "keskitorjuja": "Middle Blocker",
    "nahrávač": "Setter",
    "nahravac": "Setter",
    "univerzál": "Opposite",
    "univerzal": "Opposite",
    "smečař": "Outside Hitter",
    "smecar": "Outside Hitter",
    "smečiar": "Outside Hitter",
    "blokař": "Middle Blocker",
    "blokar": "Middle Blocker",
    "blokár": "Middle Blocker",
}

KNOWN_BIO_LABELS = {
    "shirt number",
    "club",
    "team",
    "position",
    "role",
    "heigth",
    "height",
    "înălțime",
    "inaltime",
    "ύψος",
    "výška",
    "vyska",
    "date of birth",
    "birth date",
    "birth place",
    "ημ. γέννησης",
    "datum narození",
    "ročník",
    "sport nationality",
    "nationality",
    "εθνικότητα",
    "státní příslušnost",
    "post",
    "reach",
    "jump",
    "spike",
    "block",
    "history",
    "period",
    "notes",
    "statistics",
}


def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def fetch_html(session, url, timeout=25):
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        print(f"[WARN] Failed to fetch {url}: {exc}")
        return ""


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def to_float(val):
    if val is None:
        return 0.0
    s = str(val).strip().replace("%", "").replace(",", ".")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def to_int(val):
    return int(round(to_float(val)))


def normalize_position(raw):
    text = clean_text(raw)
    low = text.lower()
    for key, mapped in ROLE_MAP.items():
        if key in low:
            return mapped
    return text if text else "Unknown"


def extract_query_param(url, param_name):
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for k, v in qs.items():
        if k.lower() == param_name.lower() and v:
            return v[0]
    return ""


def normalize_header(h):
    return re.sub(r"\s+", " ", clean_text(h).lower())


def find_col(headers, exact_candidates, start_idx=0, end_idx=None):
    end_idx = len(headers) if end_idx is None else min(end_idx, len(headers))
    for cand in exact_candidates:
        c_norm = normalize_header(cand)
        c_ns = c_norm.replace(" ", "")
        for i in range(start_idx, end_idx):
            h = headers[i]
            if h == c_norm or h.replace(" ", "") == c_ns:
                return i
    return None


def cell_val(cells, idx, default=""):
    if idx is not None and 0 <= idx < len(cells):
        return cells[idx]
    return default


def resolve_dataproject_stats_urls(session, league):
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", "")).strip()

    urls = []
    if pid:
        urls.append(f"{base}/Statistics_AllPlayers.aspx?ID={cid}&PID={pid}")
    urls.append(f"{base}/Statistics_AllPlayers.aspx?ID={cid}")

    if not pid:
        home_html = html.unescape(fetch_html(session, f"{base}/CompetitionHome.aspx?ID={cid}"))
        if home_html:
            for m in re.findall(r"Statistics(?:_AllPlayers)?\.aspx\?[^\"'<>\s)]+", home_html, re.IGNORECASE):
                found_pid = extract_query_param(urljoin(base + "/", m), "PID")
                if found_pid:
                    cand = f"{base}/Statistics_AllPlayers.aspx?ID={cid}&PID={found_pid}"
                    if cand not in urls:
                        urls.insert(0, cand)
                    break
    return urls


def parse_radgrid_page(soup, league):
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])

    headers = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr")[:5]:
            h_cells = [normalize_header(c.get_text()) for c in tr.find_all(["th", "td"])]
            if "playerid" in h_cells or ("player" in h_cells and "points" in h_cells):
                headers = h_cells
                break
        if headers:
            break

    if not headers:
        return {}, 0

    i_pid = find_col(headers, ["playerid", "player id", "id"])
    i_player = find_col(headers, ["player", "jucător", "jucator", "igrac", "igrač", "hráč", "играч", "παίκτης"])
    i_surname = find_col(headers, ["surname", "last name"])
    i_name = find_col(headers, ["name", "first name"])
    i_team = find_col(headers, ["team", "echipa", "club", "klub", "družstvo", "tým", "отбор", "ομάδα"])
    i_country = find_col(headers, ["country", "nation", "nat"])
    i_matches = find_col(headers, ["matches", "meciuri", "played", "m", "z"])
    i_sets = find_col(headers, ["sets", "seturi", "s"])
    i_points = find_col(headers, ["points", "puncte", "pts", "tot", "b"])
    i_bp = find_col(headers, ["bp", "break points"])
    i_wl = find_col(headers, ["w-p", "w-l", "wl"])

    i_s_err = find_col(headers, ["s =", "s=", "s err"])
    i_s_ok = find_col(headers, ["s !", "s!"])
    i_s_slash = find_col(headers, ["s /", "s/"])
    i_s_neg = find_col(headers, ["s -", "s-"])
    i_s_pos = find_col(headers, ["s +", "s+"])
    i_s_ace = find_col(headers, ["s #", "s#", "s pts", "aces", "ace"])

    i_r_err = find_col(headers, ["r =", "r=", "r err"])
    i_r_ok = find_col(headers, ["r !", "r!"])
    i_r_slash = find_col(headers, ["r /", "r/"])
    i_r_neg = find_col(headers, ["r -", "r-"])
    i_r_pos = find_col(headers, ["r +",
