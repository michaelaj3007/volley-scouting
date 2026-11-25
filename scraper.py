import html
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from canada_scraper import scrape_canada_usports
from college_scraper import scrape_college_league
from czech_scraper import scrape_cvf_cz_league
from volleybox_enricher import enrich_missing_bios_from_volleybox

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

EXACT_ROLE_MAP = {
    "s": "Setter", "p": "Setter", "set": "Setter",
    "opp": "Opposite", "op": "Opposite", "rs": "Opposite", "u": "Opposite", "d": "Opposite",
    "oh": "Outside Hitter", "ws": "Outside Hitter", "ls": "Outside Hitter", "os": "Outside Hitter",
    "m": "Middle Blocker", "mb": "Middle Blocker", "mh": "Middle Blocker", "c": "Middle Blocker",
    "l": "Libero", "lb": "Libero", "lib": "Libero", "ds": "Libero", "l/ds": "Libero",
}

ROLE_MAP = {
    "feladóátló": "Opposite", "feladoatlo": "Opposite",
    "högerspiker": "Opposite", "hogerspiker": "Opposite",
    "vänsterspiker": "Outside Hitter", "vansterspiker": "Outside Hitter",
    "opposite": "Opposite", "universal": "Opposite", "diagonal": "Opposite",
    "setter": "Setter", "outside": "Outside Hitter", "wing": "Outside Hitter",
    "receiver": "Outside Hitter", "spiker": "Outside Hitter",
    "middle": "Middle Blocker", "blocker": "Middle Blocker", "libero": "Libero",
    "πασαδόρος": "Setter", "διαγώνιος": "Opposite", "ακραίος": "Outside Hitter",
    "κεντρικός": "Middle Blocker", "λίμπερο": "Libero",
    "ridicator": "Setter", "ridicător": "Setter", "fals": "Opposite",
    "extremă": "Outside Hitter", "extrema": "Outside Hitter", "secund": "Outside Hitter", "centru": "Middle Blocker",
    "palleggiatore": "Setter", "opposto": "Opposite", "schiacciatore": "Outside Hitter", "centrale": "Middle Blocker",
    "passare": "Setter", "center": "Middle Blocker",
    "feladó": "Setter", "felado": "Setter", "átló": "Opposite", "négyesütő": "Outside Hitter", "szélső": "Outside Hitter", "liberó": "Libero",
    "dizač": "Setter", "korektor": "Opposite", "primač": "Outside Hitter", "srednji": "Middle Blocker",
    "разпределител": "Setter", "диагонал": "Opposite", "посрещач": "Outside Hitter", "център": "Middle Blocker", "либеро": "Libero",
    "passari": "Setter", "hakkuri": "Opposite", "yleispelaaja": "Outside Hitter", "keskipelaaja": "Middle Blocker", "keskitorjuja": "Middle Blocker",
    "nahrávač": "Setter", "univerzál": "Opposite", "smečař": "Outside Hitter", "smečiar": "Outside Hitter", "blokař": "Middle Blocker", "blokár": "Middle Blocker",
}

SHIRT_LABELS = ("shirt number", "tröjnummer", "mez száma", "mezszám", "číslo dresu", "pelinumero", "αριθμός", "número")
CLUB_LABELS = ("club", "team", "förening", "egyesület", "seura", "klub")
POS_LABELS = ("position", "role", "roll", "poszt", "pelipaikka", "pozícia", "pozicia", "pozicija", "post", "пост", "θέση")
HEIGHT_LABELS = ("heigth", "height", "längd", "magasság", "pituus", "výška", "vyska", "visina", "înălțime", "inaltime", "ύψος", "ръст")
BIRTH_LABELS = ("date of birth", "birth date", "födelsedata", "születési dátum", "syntymäaika", "dátum narodenia", "datum narození", "ημ. γέννησης")
NAT_LABELS = ("sport nationality", "nationality", "urheilukansallisuus", "národnosť", "narodnost", "nationalitet", "εθνικότητα")

KNOWN_BIO_LABELS = set(
    SHIRT_LABELS + CLUB_LABELS + POS_LABELS + HEIGHT_LABELS + BIRTH_LABELS + NAT_LABELS
    + ("birth place", "moderklubb", "születési hely", "syntymäpaikka", "miesto narodenia", "reach", "jump", "räckhöjd", "spikehöjd", "kurotus", "ulottuvuus", "dosah", "history", "historik", "historia", "notes", "statistics")
)


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
    return re.sub(r"\s+", " ", str(text or "")).strip()


def strip_accents(text):
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def name_match_key(name):
    ascii_n = strip_accents(clean_text(name)).lower()
    tokens = [t for t in re.sub(r"[^a-z0-9\s]", " ", ascii_n).split() if t]
    return tuple(sorted(tokens))


def slugify_text(text):
    ascii_n = strip_accents(clean_text(text)).lower()
    return re.sub(r"[^a-z0-9]+", "_", ascii_n).strip("_") or "unknown"


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
    cleaned = clean_text(raw)
    if not cleaned:
        return "Unknown"
    low = cleaned.lower().replace(".", "").strip()
    if low in EXACT_ROLE_MAP:
        return EXACT_ROLE_MAP[low]
    for part in re.split(r"[/,\-]", low):
        p = part.strip()
        if p in EXACT_ROLE_MAP:
            return EXACT_ROLE_MAP[p]
    for key, mapped in ROLE_MAP.items():
        if key in low:
            return mapped
    return cleaned


def first_bio(bio, keys):
    for k in keys:
        if bio.get(k):
            return bio[k]
    return ""


def extract_query_param(url, param_name):
    qs = parse_qs(urlparse(url).query)
    for k, v in qs.items():
        if k.lower() == param_name.lower() and v:
            return v[0]
    return ""


def normalize_header(h):
    s = strip_accents(clean_text(h)).lower().replace(".", " ")
    return re.sub(r"\s+", " ", s).strip()


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
    return cells[idx] if (idx is not None and 0 <= idx < len(cells)) else default


def resolve_dataproject_stats_urls(session, league):
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", "")).strip()
    urls = [f"{base}/Statistics_AllPlayers.aspx?ID={cid}&PID={pid}"] if pid else []
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
    target_table = None
    headers = []
    for table in soup.find_all("table"):
        for tr in table.find_all("tr")[:5]:
            h_cells = [normalize_header(c.get_text()) for c in tr.find_all(["th", "td"])]
            h_ns = [c.replace(" ", "") for c in h_cells]
            if "playerid" in h_ns or sum(1 for c in h_ns if c.endswith("#")) >= 3:
                headers = h_cells
                target_table = table
                break
        if headers:
            break
    if not headers:
        return {}

    h_ns = [c.replace(" ", "") for c in headers]
    i_pid = find_col(headers, ["playerid", "player id", "id"])
    i_player = find_col(headers, ["player", "jucator", "igrac", "hrac", "играч", "παικτης", "spelare", "jatekos", "pelaaja"])
    i_surname = find_col(headers, ["surname", "last name"])
    i_name = find_col(headers, ["name", "first name"])
    i_team = find_col(headers, ["team", "echipa", "club", "klub", "klubb", "forening", "druzstvo", "tym", "lag", "csapat", "egyesulet", "joukkue", "seura", "ekipa", "tim", "отбор", "ομαδα"])
    i_country = find_col(headers, ["country", "nation", "nat", "land", "orszag", "krajina", "maa", "drzava"])

    eq_cols = [i for i, c in enumerate(h_ns) if c.endswith("=")]
    hash_cols = [i for i, c in enumerate(h_ns) if c.endswith("#") or c in ("aces", "ace", "blk", "blocks", "block")]

    i_s_err = eq_cols[0] if len(eq_cols) >= 1 else None
    i_r_err = eq_cols[1] if len(eq_cols) >= 2 else None
    i_a_err = eq_cols[2] if len(eq_cols) >= 3 else None
    i_b_err = eq_cols[3] if len(eq_cols) >= 4 else None

    i_s_ace = hash_cols[0] if len(hash_cols) >= 1 else None
    i_r_perf = hash_cols[1] if len(hash_cols) >= 2 else None
    i_a_pts = hash_cols[2] if len(hash_cols) >= 3 else None
    i_b_pts = hash_cols[3] if len(hash_cols) >= 4 else None

    def find_symbol(sym, start_i, end_i):
        if start_i is None or end_i is None:
            return None
        for idx in range(start_i + 1, end_i):
            if h_ns[idx].endswith(sym):
                return idx
        return None

    i_s_ok, i_s_slash, i_s_neg, i_s_pos = find_symbol("!", i_s_err, i_s_ace), find_symbol("/", i_s_err, i_s_ace), find_symbol("-", i_s_err, i_s_ace), find_symbol("+", i_s_err, i_s_ace)
    i_r_ok, i_r_slash, i_r_neg, i_r_pos = find_symbol("!", i_r_err, i_r_perf), find_symbol("/", i_r_err, i_r_perf), find_symbol("-", i_r_err, i_r_perf), find_symbol("+", i_r_err, i_r_perf)
    i_a_ok, i_a_blk, i_a_neg, i_a_pos = find_symbol("!", i_a_err, i_a_pts), find_symbol("/", i_a_err, i_a_pts), find_symbol("-", i_a_err, i_a_pts), find_symbol("+", i_a_err, i_a_pts)

    r_start, r_end = (i_r_perf or 0), (i_a_err or len(headers))
    i_r_pos_pct = find_col(headers, ["pos%", "r pos%"], r_start, r_end)
    i_r_perf_pct = find_col(headers, ["exc %", "exc%", "perf%"], r_start, r_end)

    a_start, a_end = (i_a_pts or 0), (i_b_err or len(headers))
    i_a_pct = find_col(headers, ["exc %", "exc%", "a %", "atk %"], a_start, a_end)

    meta_start = max([idx for idx in [i_pid, i_player, i_surname, i_name, i_team, i_country] if idx is not None] or [1]) + 1
    meta_end = i_s_err if i_s_err is not None else len(headers)

    i_matches = find_col(headers, ["matches", "match", "matcher", "meciuri", "played", "merkozes", "merkozesek", "merk", "meccs", "meccsek", "zapasy", "zapas", "zap", "ottelut", "ottelu", "ott", "utakmice", "ut", "мачове", "мач", "αγωνες", "m", "z", "o", "u"], meta_start, meta_end)
    i_sets = find_col(headers, ["sets", "set", "seturi", "sety", "setovi", "szett", "szettek", "jatszma", "jatszmak", "erat", "era", "геймове", "гейм", "σετ", "s", "j", "e"], meta_start, meta_end)
    i_points = find_col(headers, ["points", "point", "pts", "poang", "pont", "pontok", "body", "bod", "pisteet", "piste", "pist", "puncte", "poeni", "bodovi", "точки", "ποντοι", "tot", "total", "ossz", "b", "p"], meta_start, meta_end)
    i_bp = find_col(headers, ["bp", "break points"], meta_start, meta_end)
    i_wl = find_col(headers, ["w-p", "w-l", "wl", "v-p", "+/-"], meta_start, meta_end)

    data_rows = target_table.select("tr.rgRow, tr.rgAltRow") if target_table else []
    if not data_rows:
        data_rows = soup.select("tr.rgRow, tr.rgAltRow")

    if i_s_err is not None and data_rows:
        if i_points is None:
            best_col, best_hits = None, 0
            for c in range(meta_start, meta_end):
                hits = sum(
                    1 for tr in data_rows[:12]
                    for cs in [[clean_text(x.get_text()) for x in tr.find_all(["td", "th"])]]
                    if (to_int(cell_val(cs, i_a_pts)) + to_int(cell_val(cs, i_s_ace)) + to_int(cell_val(cs, i_b_pts))) > 0
                    and to_int(cell_val(cs, c)) == (to_int(cell_val(cs, i_a_pts)) + to_int(cell_val(cs, i_s_ace)) + to_int(cell_val(cs, i_b_pts)))
                )
                if hits > best_hits:
                    best_hits, best_col = hits, c
            if best_col is not None and best_hits >= 2:
                i_points = best_col
            elif i_s_err - 1 >= meta_start:
                i_points = i_s_err - 1
        if i_sets is None and i_points is not None and i_points - 1 >= meta_start:
            i_sets = i_points - 1
        if i_matches is None and i_sets is not None and i_sets - 1 >= meta_start:
            i_matches = i_sets - 1

    page_players = {}
    for tr in data_rows:
        cells = [clean_text(c.get_text()) for c in tr.find_all(["td", "th"])]
        if len(cells) < 10:
            continue
        pl_id = re.sub(r"[^0-9]", "", cell_val(cells, i_pid))
        if not pl_id:
            link = tr.find("a", href=re.compile(r"PlayerID=", re.IGNORECASE))
            if link:
                pl_id = extract_query_param(urljoin(base + "/", link["href"]), "PlayerID")
        if not pl_id:
            continue

        first_n, last_n = cell_val(cells, i_name), cell_val(cells, i_surname)
        full_name = f"{last_n} {first_n}".strip() if (first_n or last_n) else re.sub(r"\s*\([^)]*\)\s*$", "", cell_val(cells, i_player)).strip()

        s_err, s_ok, s_slash, s_neg, s_pos, aces = to_int(cell_val(cells, i_s_err)), to_int(cell_val(cells, i_s_ok)), to_int(cell_val(cells, i_s_slash)), to_int(cell_val(cells, i_s_neg)), to_int(cell_val(cells, i_s_pos)), to_int(cell_val(cells, i_s_ace))
        s_tot = s_err + s_ok + s_slash + s_neg + s_pos + aces

        r_err, r_ok, r_slash, r_neg, r_pos, r_perf = to_int(cell_val(cells, i_r_err)), to_int(cell_val(cells, i_r_ok)), to_int(cell_val(cells, i_r_slash)), to_int(cell_val(cells, i_r_neg)), to_int(cell_val(cells, i_r_pos)), to_int(cell_val(cells, i_r_perf))
        r_tot = r_err + r_ok + r_slash + r_neg + r_pos + r_perf
        rec_pos_pct = to_float(cell_val(cells, i_r_pos_pct)) or (round(100.0 * (r_pos + r_perf) / r_tot, 1) if r_tot > 0 else 0.0)
        rec_perf_pct = to_float(cell_val(cells, i_r_perf_pct)) or (round(100.0 * r_perf / r_tot, 1) if r_tot > 0 else 0.0)

        a_err, a_ok, a_blk, a_neg, a_pos, a_pts = to_int(cell_val(cells, i_a_err)), to_int(cell_val(cells, i_a_ok)), to_int(cell_val(cells, i_a_blk)), to_int(cell_val(cells, i_a_neg)), to_int(cell_val(cells, i_a_pos)), to_int(cell_val(cells, i_a_pts))
        a_tot = a_err + a_ok + a_blk + a_neg + a_pos + a_pts
        atk_pct = to_float(cell_val(cells, i_a_pct)) or (round(100.0 * a_pts / a_tot, 1) if a_tot > 0 else 0.0)
        atk_eff_pct = round(100.0 * (a_pts - a_err - a_blk) / a_tot, 1) if a_tot > 0 else 0.0

        blocks = to_int(cell_val(cells, i_b_pts))
        calc_pts = a_pts + aces + blocks
        raw_pts = to_int(cell_val(cells, i_points))
        points = raw_pts if raw_pts >= calc_pts else calc_pts

        page_players[pl_id] = {
            "pl_id": pl_id, "url": f"{base}/PlayerDetails.aspx?ID={cid}&PlayerID={pl_id}",
            "name": full_name, "team": cell_val(cells, i_team), "country": cell_val(cells, i_country),
            "matches": to_int(cell_val(cells, i_matches)), "sets": to_int(cell_val(cells, i_sets)),
            "points": points, "break_points": to_int(cell_val(cells, i_bp)), "won_lost": to_int(cell_val(cells, i_wl)),
            "serve_total": s_tot, "aces": aces, "serve_errors": s_err,
            "reception_total": r_tot, "reception_pos_pct": rec_pos_pct, "reception_perf_pct": rec_perf_pct,
            "attack_total": a_tot, "attack_points": a_pts, "attack_errors": a_err, "attack_blocked": a_blk,
            "attack_pct": atk_pct, "attack_eff_pct": atk_eff_pct, "blocks": blocks,
        }
    return page_players


def _safe_page_content(page):
    for _ in range(5):
        try:
            return page.content()
        except Exception:
            time.sleep(0.4)
    return ""


def _wait_for_radgrid_change(page, league, prev_ids, timeout_sec=10.0):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(0.5)
        html_now = _safe_page_content(page)
        if not html_now:
            continue
        cand = parse_radgrid_page(BeautifulSoup(html_now, "lxml"), league)
        if cand:
            cand_ids = set(cand.keys())
            if cand_ids != prev_ids and len(cand_ids - prev_ids) > 0:
                return cand
    return None


def _click_radgrid_next(page, target_page_num, strategy):
    try:
        if strategy == "num_link":
            links = page.locator(".rgNumPart a, .rgPager a")
            cnt = links.count()
            target_str = str(target_page_num)
            ellipsis_after = None
            passed_prev = False
            for i in range(cnt):
                a = links.nth(i)
                txt = clean_text(a.inner_text() if a.count() else "")
                if txt == str(target_page_num - 1):
                    passed_prev = True
                if txt == target_str:
                    a.click(force=True, timeout=4000)
                    return True
                if passed_prev and txt == "..." and ellipsis_after is None:
                    ellipsis_after = a
            if ellipsis_after is not None:
                ellipsis_after.click(force=True, timeout=4000)
                return True
            return False

        if strategy == "next_btn_real":
            btns = page.locator("input.rgPageNext, button.rgPageNext, a.rgPageNext, [title='Next Page']")
            for i in range(btns.count()):
                b = btns.nth(i)
                is_dis = b.evaluate("el => Boolean(el.disabled || el.classList.contains('rgDisabled') || el.classList.contains('aspNetDisabled'))")
                if not is_dis:
                    b.click(force=True, timeout=4000)
                    return True
            return False

        if strategy == "telerik_api":
            return bool(page.evaluate(
                """() => {
                    if (typeof $find !== 'function') return false;
                    let bestGrid = null, maxRows = -1;
                    for (const g of document.querySelectorAll('.RadGrid')) {
                        const c = g.querySelectorAll('tr.rgRow, tr.rgAltRow').length;
                        if (c > maxRows && g.id) { maxRows = c; bestGrid = g; }
                    }
                    if (!bestGrid) return false;
                    const ctrl = $find(bestGrid.id);
                    if (!ctrl || typeof ctrl.get_masterTableView !== 'function') return false;
                    ctrl.get_masterTableView().page('Next');
                    return true;
                }"""
            ))
    except Exception:
        return False
    return False


def scrape_all_players_playwright(browser, league, candidate_urls):
    all_players = {}
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = context.new_page()
    try:
        for url in candidate_urls:
            print(f"[{league['name']}] Opening browser on {url}")
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.wait_for_function(
                    """() => Array.from(document.querySelectorAll('tr.rgRow, tr.rgAltRow')).some(r => r.querySelectorAll('td').length >= 10)""",
                    timeout=18000,
                )
            except Exception:
                continue

            html_first = _safe_page_content(page)
            batch = parse_radgrid_page(BeautifulSoup(html_first, "lxml"), league) if html_first else {}
            if not batch:
                continue

            pages_scraped = 0
            for _ in range(100):
                pages_scraped += 1
                prev_ids = set(batch.keys())
                new_count = sum(1 for k in batch if k not in all_players)
                all_players.update(batch)
                if pages_scraped > 1 and new_count == 0:
                    break

                next_batch = None
                target_num = pages_scraped + 1
                for strategy in ("num_link", "next_btn_real", "telerik_api"):
                    if _click_radgrid_next(page, target_num, strategy):
                        cand = _wait_for_radgrid_change(page, league, prev_ids, timeout_sec=8.0)
                        if cand and any(k not in all_players for k in cand):
                            next_batch = cand
                            break
                if not next_batch:
                    break
                batch = next_batch

            if all_players:
                print(f"[{league['name']}] Browser scraped {len(all_players)} players with full stats across {pages_scraped} pages.")
                break
    finally:
        context.close()
    return all_players


def discover_extra_players_from_teams(session, league, existing_players):
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", "")).strip()
    seed_urls = [f"{base}/CompetitionHome.aspx?ID={cid}", f"{base}/CompetitionTeamSearch.aspx?ID={cid}", f"{base}/CompetitionPlayerSearch.aspx?ID={cid}"]
    if pid:
        seed_urls.append(f"{base}/CompetitionStandings.aspx?ID={cid}&PID={pid}")

    team_urls, player_urls = set(), {}

    def register(raw_html):
        text = html.unescape(raw_html)
        for m in re.findall(r"CompetitionTeamDetails\.aspx\?[^\"'<>\s)]+", text, re.IGNORECASE):
            team_urls.add(urljoin(base + "/", m))
        for m in re.findall(r"PlayerDetails\.aspx\?[^\"'<>\s)]+", text, re.IGNORECASE):
            full = urljoin(base + "/", m)
            pl_id = extract_query_param(full, "PlayerID")
            if pl_id and (pl_id not in player_urls or ("teamid=" in full.lower() and "teamid=" not in player_urls[pl_id].lower())):
                player_urls[pl_id] = full
        for pl_id in re.findall(r"PlayerID=(\d+)", text, re.IGNORECASE):
            player_urls.setdefault(pl_id, f"{base}/PlayerDetails.aspx?ID={cid}&PlayerID={pl_id}")

    for u in seed_urls:
        register(fetch_html(session, u))
    for t_u in sorted(team_urls):
        register(fetch_html(session, t_u))

    added = 0
    for pl_id, p_url in player_urls.items():
        if pl_id in existing_players:
            if "teamid=" in p_url.lower():
                existing_players[pl_id]["url"] = p_url
        else:
            existing_players[pl_id] = {
                "pl_id": pl_id, "url": p_url, "name": "", "team": "", "country": "",
                "matches": 0, "sets": 0, "points": 0, "break_points": 0, "won_lost": 0,
                "serve_total": 0, "aces": 0, "serve_errors": 0,
                "reception_total": 0, "reception_pos_pct": 0.0, "reception_perf_pct": 0.0,
                "attack_total": 0, "attack_points": 0, "attack_errors": 0, "attack_blocked": 0,
                "attack_pct": 0.0, "attack_eff_pct": 0.0, "blocks": 0,
            }
            added += 1
    print(f"[{league['name']}] Found {len(team_urls)} teams, added {added} extra roster players.")
    return existing_players


def enrich_player_profile(league, pl_id, data):
    session = get_session()
    raw_html = fetch_html(session, data["url"])
    name, team, nationality = data["name"], data["team"], data["country"]
    raw_pos, height_cm, birth_year, age, photo_url = "", 0, 0, 0, ""

    if raw_html:
        soup = BeautifulSoup(raw_html, "lxml")
        lines = [clean_text(x) for x in soup.get_text("\n").splitlines() if clean_text(x)]
        bio, name_guess = {}, ""
        for i, line in enumerate(lines):
            low = line.rstrip(":").lower()
            if low in SHIRT_LABELS and i > 0 and not name_guess:
                for back in range(1, min(4, i + 1)):
                    cand = re.sub(r"^\d+\s+", "", lines[i - back]).strip()
                    if cand and len(cand) >= 3 and cand.lower() not in KNOWN_BIO_LABELS:
                        name_guess = cand
                        break
            if i < len(lines) - 1 and low in KNOWN_BIO_LABELS:
                nxt = lines[i + 1]
                if nxt.rstrip(":").lower() not in KNOWN_BIO_LABELS and nxt not in ("-", "—", "N/A"):
                    bio.setdefault(low, nxt)

        if not name:
            el_sur = soup.find(id=re.compile(r"LBL_Surname|PlayerSurname", re.IGNORECASE))
            el_nam = soup.find(id=re.compile(r"LBL_Name|PlayerName", re.IGNORECASE))
            if el_sur or el_nam:
                name = f"{clean_text(el_sur.get_text()) if el_sur else ''} {clean_text(el_nam.get_text()) if el_nam else ''}".strip()
            if not name:
                name = name_guess
        if not team:
            team = first_bio(bio, CLUB_LABELS)
        raw_pos = first_bio(bio, POS_LABELS)
        h_val = to_int(first_bio(bio, HEIGHT_LABELS))
        if 140 <= h_val <= 235:
            height_cm = h_val
        ym = re.search(r"(19\d{2}|20[0-1]\d)", first_bio(bio, BIRTH_LABELS))
        if ym:
            birth_year = int(ym.group(1))
            age = datetime.now().year - birth_year
        if not nationality:
            nationality = first_bio(bio, NAT_LABELS)
        img = soup.find("img", id=re.compile(r"player|photo|img", re.IGNORECASE))
        if img and img.get("src"):
            photo_url = urljoin(league["base_url"].rstrip("/") + "/", img["src"])

    sets = data["sets"]
    return {
        "player_id": f"{league['country']}_{league['competition_id']}_{pl_id}",
        "season": league.get("season", "2026-27"),
        "league": league["name"],
        "country": league["country"],
        "gender": league.get("gender", ""),
        "name": name or f"Player {pl_id}",
        "team": team,
        "position": normalize_position(raw_pos),
        "raw_position": raw_pos,
        "class_year": "",
        "grad_status": "",
        "grad_2027": False,
        "height_cm": height_cm,
        "birth_year": birth_year,
        "age": age,
        "nationality": nationality,
        "matches": data["matches"],
        "sets": sets,
        "points": data["points"],
        "points_per_set": round(data["points"] / sets, 2) if sets > 0 else 0.0,
        "break_points": data["break_points"],
        "won_lost": data["won_lost"],
        "serve_total": data["serve_total"],
        "aces": data["aces"],
        "aces_per_set": round(data["aces"] / sets, 2) if sets > 0 else 0.0,
        "serve_errors": data["serve_errors"],
        "blocks": data["blocks"],
        "blocks_per_set": round(data["blocks"] / sets, 2) if sets > 0 else 0.0,
        "attack_total": data["attack_total"],
        "attack_points": data["attack_points"],
        "attack_errors": data["attack_errors"],
        "attack_blocked": data["attack_blocked"],
        "attack_pct": data["attack_pct"],
        "attack_eff_pct": data["attack_eff_pct"],
        "reception_total": data["reception_total"],
        "reception_pos_pct": data["reception_pos_pct"],
        "reception_perf_pct": data["reception_perf_pct"],
        "profile_url": data["url"],
        "photo_url": photo_url,
    }


def _classify_ranking_span(span_id, page_title, main_txt, rt):
    sid = (span_id or "").lower()
    title = normalize_header(page_title)
    is_pct = "%" in main_txt or "perc" in sid or "pct" in sid

    # 1) Primary classification from the standardized DataProject span ID (language-independent)
    if "scorer" in sid or "point" in sid:
        return "points_set" if "set" in sid else "points_total"
    if any(k in sid for k in ("server", "service", "ace")):
        if "set" in sid:
            return "aces_set"
        return "aces_pct" if is_pct else "aces_total"
    if "block" in sid or "wall" in sid or "mur" in sid:
        return "blocks_set" if "set" in sid else "blocks_total"
    if any(k in sid for k in ("spik", "attack", "kill")):
        if "eff" in sid:
            return "attack_eff"
        if is_pct:
            return "attack_pct"
        return "attack_set" if "set" in sid else "attack_total"
    if "receiv" in sid or "rec" in sid:
        return "reception_perf" if any(k in sid for k in ("perf", "exc")) else "reception_pos"

    # 2) Secondary classification from page title
    if any(k in title for k in ("recep", "vastaa", "υποδοχ", "preluar", "mottagn", "fogad", "prijem", "посрещ")):
        return "reception_perf" if any(k in title for k in ("perf", "exc", "erinom")) else "reception_pos"
    if any(k in title for k in ("block", "torjun", "μπλοκ", "blocaj", "sanc", "blok", "блок")):
        return "blocks_set" if any(k in title for k in ("set", "era", "σετ", "szett")) else "blocks_total"
    if any(k in title for k in ("ace", "serv", "assat", "syot", "ασοι", "σερβ", "servici", "ess", "asz", "nyit", "асов", "серв")):
        return "aces_set" if any(k in title for k in ("set", "era", "σετ", "szett")) else "aces_total"
    if any(k in title for k in ("spik", "attack", "hyok", "επιθ", "atac", "anfall", "tamad", "napad", "атак")):
        return "attack_pct" if is_pct else "attack_total"
    if any(k in title for k in ("scorer", "point", "piste", "ποντ", "punct", "poang", "pont", "poen", "bod", "точк")):
        return "points_set" if any(k in title for k in ("set", "era", "σετ", "szett")) else "points_total"

    # 3) Fallback by standard DataProject rt index
    rt_defaults = {
        1: "points_set",
        2: "points_total",
        3: "aces_set",
        4: "aces_total",
        5: "blocks_set",
        6: "blocks_total",
        7: "attack_pct",
        8: "reception_pos",
    }
    return rt_defaults.get(rt, "")


def _parse_ranking_complete_items(soup):
    items = []
    # Match both first position (DIV_First_Position) and other positions (DIV_Other_Positions)
    containers = soup.find_all("div", id=re.compile(r"LV_Ranking_ctrl\d+_DIV_", re.IGNORECASE))
    if not containers:
        seen = set()
        for nm in soup.select(".Ranking_PlayerName, [id*='LBL_NameSurname']"):
            parent = nm.parent
            for _ in range(4):
                if parent and parent.select_one(".Ranking_PlayerData, [id*='LBL_Best']"):
                    if id(parent) not in seen:
                        seen.add(id(parent))
                        containers.append(parent)
                    break
                parent = parent.parent if parent else None

    for box in containers:
        name_el = box.select_one(".Ranking_PlayerName, [id*='LBL_NameSurname']")
        data_el = box.select_one(".Ranking_PlayerData, [id*='LBL_Best']")
        if not name_el or not data_el:
            continue

        name = clean_text(name_el.get_text(" "))
        if not name:
            continue

        det_el = data_el.select_one(".Ranking_PlayerDataDetails")
        det_txt = clean_text(det_el.get_text(" ")) if det_el else ""
        full_data_txt = clean_text(data_el.get_text(" "))
        main_txt = clean_text(full_data_txt.replace(det_txt, "", 1)) if det_txt else full_data_txt

        # Parse (num/den) or (single) from details like "(125/26)"
        m_frac = re.search(r"(\d+)\s*/\s*(\d+)", det_txt)
        f_num, f_den = (int(m_frac.group(1)), int(m_frac.group(2))) if m_frac else (0, 0)
        m_single = re.search(r"\((\d+)\)", det_txt) if not m_frac else None
        s_val = int(m_single.group(1)) if m_single else 0

        items.append({
            "name": name,
            "span_id": data_el.get("id") or "",
            "main_txt": main_txt,
            "main_val": to_float(main_txt),
            "f_num": f_num,
            "f_den": f_den,
            "s_val": s_val,
        })
    return items


def apply_best_player_rankings_to_records(session, league, league_records):
    # Enrich all league players using DataProject's complete rankings (rt=1..10)
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", "")).strip()

    by_key = {}
    for rec in league_records:
        nk = name_match_key(rec["name"])
        if nk:
            by_key.setdefault(nk, rec)

    tables_used = 0
    matched_ids = set()
    created_new = 0

    for rt in range(1, 11):
        urls = [f"{base}/BestPlayerRankingComplete.aspx?ID={cid}&rt={rt}"]
        if pid:
            urls.append(f"{base}/BestPlayerRankingComplete.aspx?ID={cid}&PID={pid}&rt={rt}")

        items = []
        page_title = ""
        for u in urls:
            raw_html = fetch_html(session, u, timeout=20)
            if not raw_html:
                continue
            soup = BeautifulSoup(raw_html, "lxml")
            items = _parse_ranking_complete_items(soup)
            if items:
                title_els = soup.select("h1, h2, h3, [id*='Title'], [id*='RankingTitle'], [class*='Title']")
                page_title = " ".join(clean_text(el.get_text(" ")) for el in title_els[:4])
                break

        if not items:
            continue

        tables_used += 1
        sample_cat = _classify_ranking_span(items[0]["span_id"], page_title, items[0]["main_txt"], rt)
        print(f"[{league['name']}] Ranking rt={rt} ({sample_cat}, span={items[0]['span_id']}): {len(items)} players")

        for it in items:
            cat = _classify_ranking_span(it["span_id"], page_title, it["main_txt"], rt)
            if not cat:
                continue

            nk = name_match_key(it["name"])
            if not nk:
                continue

            rec = by_key.get(nk)
            if rec is None:
                rec = {
                    "player_id": f"{league['country']}_{cid}_rk_{slugify_text(it['name'])}",
                    "season": league.get("season", "2026-27"),
                    "league": league["name"],
                    "country": league["country"],
                    "gender": league.get("gender", ""),
                    "name": it["name"],
                    "team": "",
                    "position": "Unknown",
                    "raw_position": "",
                    "class_year": "",
                    "grad_status": "",
                    "grad_2027": False,
                    "height_cm": 0,
                    "birth_year": 0,
                    "age": 0,
                    "nationality": "",
                    "matches": 0, "sets": 0, "points": 0, "points_per_set": 0.0,
                    "break_points": 0, "won_lost": 0,
                    "serve_total": 0, "aces": 0, "aces_per_set": 0.0, "serve_errors": 0,
                    "blocks": 0, "blocks_per_set": 0.0,
                    "attack_total": 0, "attack_points": 0, "attack_errors": 0, "attack_blocked": 0,
                    "attack_pct": 0.0, "attack_eff_pct": 0.0,
                    "reception_total": 0, "reception_pos_pct": 0.0, "reception_perf_pct": 0.0,
                    "profile_url": f"{base}/BestPlayerRankingComplete.aspx?ID={cid}&rt={rt}",
                    "photo_url": "",
                }
                league_records.append(rec)
                by_key[nk] = rec
                created_new += 1

            matched_ids.add(rec["player_id"])
            val, f_num, f_den, s_val = it["main_val"], it["f_num"], it["f_den"], it["s_val"]

            if cat == "points_set":
                if f_num > 0:
                    rec["points"] = max(rec["points"], f_num)
                if f_den > 0:
                    rec["sets"] = max(rec["sets"], f_den)
                if val > 0 and rec["points_per_set"] == 0.0:
                    rec["points_per_set"] = round(val, 2)
            elif cat == "points_total":
                rec["points"] = max(rec["points"], f_num or int(round(val)))
                if f_den > 0:
                    rec["sets"] = max(rec["sets"], f_den)
                elif s_val > 0 and s_val <= 150:
                    rec["sets"] = max(rec["sets"], s_val)
            elif cat == "aces_set":
                if f_num > 0:
                    rec["aces"] = max(rec["aces"], f_num)
                if f_den > 0:
                    rec["sets"] = max(rec["sets"], f_den)
                if val > 0 and rec["aces_per_set"] == 0.0:
                    rec["aces_per_set"] = round(val, 2)
            elif cat == "aces_total":
                rec["aces"] = max(rec["aces"], f_num or int(round(val)))
                if f_den > 0:
                    if rec["sets"] == 0 and f_den <= 150:
                        rec["sets"] = f_den
                    else:
                        rec["serve_total"] = max(rec["serve_total"], f_den)
            elif cat == "aces_pct":
                if f_num > 0:
                    rec["aces"] = max(rec["aces"], f_num)
                if f_den > 0:
                    rec["serve_total"] = max(rec["serve_total"], f_den)
            elif cat == "blocks_set":
                if f_num > 0:
                    rec["blocks"] = max(rec["blocks"], f_num)
                if f_den > 0:
                    rec["sets"] = max(rec["sets"], f_den)
                if val > 0 and rec["blocks_per_set"] == 0.0:
                    rec["blocks_per_set"] = round(val, 2)
            elif cat == "blocks_total":
                rec["blocks"] = max(rec["blocks"], f_num or int(round(val)))
                if f_den > 0:
                    rec["sets"] = max(rec["sets"], f_den)
                elif s_val > 0 and s_val <= 150:
                    rec["sets"] = max(rec["sets"], s_val)
            elif cat == "attack_pct":
                if rec["attack_pct"] == 0.0 and 0 < val <= 100:
                    rec["attack_pct"] = round(val, 1)
                if f_num > 0:
                    rec["attack_points"] = max(rec["attack_points"], f_num)
                if f_den > 0:
                    rec["attack_total"] = max(rec["attack_total"], f_den)
            elif cat == "attack_eff":
                if rec["attack_eff_pct"] == 0.0 and -100 <= val <= 100:
                    rec["attack_eff_pct"] = round(val, 1)
                if f_num > 0:
                    rec["attack_points"] = max(rec["attack_points"], f_num)
                if f_den > 0:
                    rec["attack_total"] = max(rec["attack_total"], f_den)
            elif cat == "attack_set":
                if f_num > 0:
                    rec["attack_points"] = max(rec["attack_points"], f_num)
                if f_den > 0:
                    rec["sets"] = max(rec["sets"], f_den)
            elif cat == "attack_total":
                rec["attack_points"] = max(rec["attack_points"], f_num or int(round(val)))
                if f_den > 0:
                    rec["attack_total"] = max(rec["attack_total"], f_den)
            elif cat == "reception_pos":
                if rec["reception_pos_pct"] == 0.0 and 0 < val <= 100:
                    rec["reception_pos_pct"] = round(val, 1)
                if f_den > 0:
                    rec["reception_total"] = max(rec["reception_total"], f_den)
                elif s_val > 0:
                    rec["reception_total"] = max(rec["reception_total"], s_val)
            elif cat == "reception_perf":
                if rec["reception_perf_pct"] == 0.0 and 0 < val <= 100:
                    rec["reception_perf_pct"] = round(val, 1)
                if f_den > 0:
                    rec["reception_total"] = max(rec["reception_total"], f_den)

    # Recompute derived per-set & percentage metrics across all players
    for rec in league_records:
        calc_pts = rec["attack_points"] + rec["aces"] + rec["blocks"]
        if calc_pts > rec["points"]:
            rec["points"] = calc_pts
        if rec["sets"] == 0 and rec["points"] > 0 and rec["points_per_set"] > 0:
            rec["sets"] = max(1, int(round(rec["points"] / rec["points_per_set"])))
        s = rec["sets"]
        if s > 0:
            rec["points_per_set"] = round(rec["points"] / s, 2)
            rec["aces_per_set"] = round(rec["aces"] / s, 2)
            rec["blocks_per_set"] = round(rec["blocks"] / s, 2)
        if rec["attack_pct"] == 0.0 and rec["attack_total"] > 0 and rec["attack_points"] > 0:
            rec["attack_pct"] = round(100.0 * rec["attack_points"] / rec["attack_total"], 1)

    print(f"[{league['name']}] BestPlayerRankingComplete updated {len(matched_ids)} players ({created_new} new) across {tables_used} ranking tables.")
    return league_records


def apply_manual_overrides(df, overrides_path="data/manual_overrides.csv"):
    if not os.path.exists(overrides_path) or df.empty:
        return df
    try:
        ov = pd.read_csv(overrides_path)
        if "player_id" not in ov.columns:
            return df
        ov_map = ov.set_index("player_id").to_dict(orient="index")
        for idx, row in df.iterrows():
            pid = row["player_id"]
            if pid in ov_map:
                h = ov_map[pid].get("height_cm", 0)
                if pd.notna(h) and int(float(h)) > 0:
                    df.at[idx, "height_cm"] = int(float(h))
                pos = ov_map[pid].get("position", "")
                if pd.notna(pos) and str(pos).strip():
                    df.at[idx, "position"] = str(pos).strip()
    except Exception as exc:
        print(f"[WARN] Could not apply manual overrides: {exc}")
    return df


def main():
    with open("leagues.json", "r", encoding="utf-8") as f:
        leagues = json.load(f)

    all_records = []
    session = get_session()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for league in leagues:
                print(f"=== Processing {league['name']} ({league.get('season', '2026-27')}) ===")
                ltype = league.get("type", "")
                if ltype == "cvf_cz":
                    recs = scrape_cvf_cz_league(browser, session, league)
                    for r in recs:
                        r.setdefault("season", league.get("season", "2026-27"))
                        r.setdefault("class_year", "")
                        r.setdefault("grad_status", "")
                        r.setdefault("grad_2027", False)
                    all_records.extend(recs)
                    continue
                if ltype == "usports_ca":
                    all_records.extend(scrape_canada_usports(browser, league))
                    continue
                if ltype == "ncaa_us":
                    all_records.extend(scrape_college_league(browser, league))
                    continue

                cand_urls = resolve_dataproject_stats_urls(session, league)
                players = scrape_all_players_playwright(browser, league, cand_urls)
                players = discover_extra_players_from_teams(session, league, players)

                league_records = []
                with ThreadPoolExecutor(max_workers=12) as executor:
                    futures = [executor.submit(enrich_player_profile, league, pl_id, pdata) for pl_id, pdata in players.items()]
                    for fut in as_completed(futures):
                        rec = fut.result()
                        if rec:
                            league_records.append(rec)

                league_records = apply_best_player_rankings_to_records(session, league, league_records)
                all_records.extend(league_records)
        finally:
            browser.close()

    os.makedirs("data", exist_ok=True)
    df = pd.DataFrame(all_records)
    if not df.empty:
        df = df.drop_duplicates(subset=["player_id"], keep="first")
        df["position"] = df["position"].apply(normalize_position)
        df = df.sort_values(by=["points", "name"], ascending=[False, True])
        df = enrich_missing_bios_from_volleybox(df, max_new_lookups=450)
        df["position"] = df["position"].apply(normalize_position)
        df = apply_manual_overrides(df)

    df.to_csv("data/players.csv", index=False, encoding="utf-8-sig")

    meta = {
        "last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_players": len(df),
        "leagues": sorted(list({l["name"] for l in leagues})),
    }
    with open("data/last_updated.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"=== DONE: Saved {len(df)} players across {len(leagues)} entries to data/players.csv ===")


if __name__ == "__main__":
    main()
