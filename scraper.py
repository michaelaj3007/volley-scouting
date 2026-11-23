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
from playwright.sync_api import sync_playwright

from czech_scraper import scrape_cvf_cz_league

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Specific/compound roles MUST appear before shorter substrings (e.g. feladóátló before feladó, högerspiker before spiker)
ROLE_MAP = {
    "feladóátló": "Opposite",
    "feladoatlo": "Opposite",
    "högerspiker": "Opposite",
    "hogerspiker": "Opposite",
    "vänsterspiker": "Outside Hitter",
    "vansterspiker": "Outside Hitter",
    "opposite": "Opposite",
    "universal": "Opposite",
    "diagonal": "Opposite",
    "setter": "Setter",
    "outside": "Outside Hitter",
    "wing": "Outside Hitter",
    "receiver": "Outside Hitter",
    "spiker": "Outside Hitter",
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
    "center": "Middle Blocker",
    "feladó": "Setter",
    "felado": "Setter",
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
    "keskipelaaja": "Middle Blocker",
    "keskitorjuja": "Middle Blocker",
    "nahrávač": "Setter",
    "univerzál": "Opposite",
    "smečař": "Outside Hitter",
    "smečiar": "Outside Hitter",
    "blokař": "Middle Blocker",
    "blokár": "Middle Blocker",
}

SHIRT_LABELS = ("shirt number", "tröjnummer", "mez száma", "mezszám", "číslo dresu", "pelinumero", "αριθμός", "número")
CLUB_LABELS = ("club", "team", "förening", "egyesület", "seura", "klub")
POS_LABELS = ("position", "role", "roll", "poszt", "pelipaikka", "pozícia", "pozicia", "pozicija", "post", "пост", "θέση")
HEIGHT_LABELS = ("heigth", "height", "längd", "magasság", "pituus", "výška", "vyska", "visina", "înălțime", "inaltime", "ύψος", "ръст")
BIRTH_LABELS = ("date of birth", "birth date", "födelsedata", "születési dátum", "syntymäaika", "dátum narodenia", "datum narození", "ημ. γέννησης")
NAT_LABELS = ("sport nationality", "nationality", "urheilukansallisuus", "národnosť", "narodnost", "nationalitet", "εθνικότητα")

KNOWN_BIO_LABELS = set(
    SHIRT_LABELS + CLUB_LABELS + POS_LABELS + HEIGHT_LABELS + BIRTH_LABELS + NAT_LABELS
    + (
        "birth place", "moderklubb", "születési hely", "syntymäpaikka", "miesto narodenia",
        "reach", "jump", "räckhöjd", "spikehöjd", "kurotus", "ulottuvuus", "dosah",
        "history", "historik", "historia", "notes", "statistics",
    )
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
    low = clean_text(raw).lower()
    for key, mapped in ROLE_MAP.items():
        if key in low:
            return mapped
    return clean_text(raw) if clean_text(raw) else "Unknown"


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
        return {}

    i_pid = find_col(headers, ["playerid", "player id", "id"])
    i_player = find_col(headers, ["player", "jucător", "jucator", "igrac", "igrač", "hráč", "играч", "παίκτης", "spelare", "játékos", "pelaaja"])
    i_surname = find_col(headers, ["surname", "last name"])
    i_name = find_col(headers, ["name", "first name"])
    i_team = find_col(headers, ["team", "echipa", "club", "klub", "družstvo", "tým", "отбор", "ομάδα", "lag", "csapat", "joukkue"])
    i_country = find_col(headers, ["country", "nation", "nat"])
    i_matches = find_col(headers, ["matches", "meciuri", "played", "m", "z"])
    i_sets = find_col(headers, ["sets", "seturi", "s"])
    i_points = find_col(headers, ["points", "puncte", "pts", "tot", "b"])
    i_bp = find_col(headers, ["bp", "break points"])
    i_wl = find_col(headers, ["w-p", "w-l", "wl"])

    i_s_err = find_col(headers, ["s =", "s="])
    i_s_ok = find_col(headers, ["s !", "s!"])
    i_s_slash = find_col(headers, ["s /", "s/"])
    i_s_neg = find_col(headers, ["s -", "s-"])
    i_s_pos = find_col(headers, ["s +", "s+"])
    i_s_ace = find_col(headers, ["s #", "s#", "s pts", "aces", "ace"])

    i_r_err = find_col(headers, ["r =", "r="])
    i_r_ok = find_col(headers, ["r !", "r!"])
    i_r_slash = find_col(headers, ["r /", "r/"])
    i_r_neg = find_col(headers, ["r -", "r-"])
    i_r_pos = find_col(headers, ["r +", "r+"])
    i_r_perf = find_col(headers, ["r #", "r#"])

    i_a_err = find_col(headers, ["a =", "a="])
    i_a_ok = find_col(headers, ["a !", "a!"])
    i_a_blk = find_col(headers, ["a /", "a/"])
    i_a_neg = find_col(headers, ["a -", "a-"])
    i_a_pos = find_col(headers, ["a +", "a+"])
    i_a_pts = find_col(headers, ["a #", "a#", "a pts", "atk pts"])

    i_b_err = find_col(headers, ["b =", "b="])
    i_b_pts = find_col(headers, ["b #", "b#", "b pts", "blk", "blocks", "block"])

    r_start = i_r_perf if i_r_perf is not None else 0
    r_end = i_a_err if i_a_err is not None else len(headers)
    i_r_pos_pct = find_col(headers, ["pos%", "r pos%"], r_start, r_end)
    i_r_perf_pct = find_col(headers, ["exc.%", "exc%", "perf%"], r_start, r_end)

    a_start = i_a_pts if i_a_pts is not None else 0
    a_end = i_b_err if i_b_err is not None else len(headers)
    i_a_pct = find_col(headers, ["exc. %", "exc.%", "exc%", "a %", "atk %"], a_start, a_end)

    page_players = {}
    for tr in soup.select("tr.rgRow, tr.rgAltRow"):
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

        first_n = cell_val(cells, i_name)
        last_n = cell_val(cells, i_surname)
        full_name = f"{last_n} {first_n}".strip() if (first_n or last_n) else re.sub(r"\s*\([^)]*\)\s*$", "", cell_val(cells, i_player)).strip()

        s_err = to_int(cell_val(cells, i_s_err))
        s_ok = to_int(cell_val(cells, i_s_ok))
        s_slash = to_int(cell_val(cells, i_s_slash))
        s_neg = to_int(cell_val(cells, i_s_neg))
        s_pos = to_int(cell_val(cells, i_s_pos))
        aces = to_int(cell_val(cells, i_s_ace))
        s_tot = s_err + s_ok + s_slash + s_neg + s_pos + aces

        r_err = to_int(cell_val(cells, i_r_err))
        r_ok = to_int(cell_val(cells, i_r_ok))
        r_slash = to_int(cell_val(cells, i_r_slash))
        r_neg = to_int(cell_val(cells, i_r_neg))
        r_pos = to_int(cell_val(cells, i_r_pos))
        r_perf = to_int(cell_val(cells, i_r_perf))
        r_tot = r_err + r_ok + r_slash + r_neg + r_pos + r_perf
        rec_pos_pct = to_float(cell_val(cells, i_r_pos_pct)) or (round(100.0 * (r_pos + r_perf) / r_tot, 1) if r_tot > 0 else 0.0)
        rec_perf_pct = to_float(cell_val(cells, i_r_perf_pct)) or (round(100.0 * r_perf / r_tot, 1) if r_tot > 0 else 0.0)

        a_err = to_int(cell_val(cells, i_a_err))
        a_ok = to_int(cell_val(cells, i_a_ok))
        a_blk = to_int(cell_val(cells, i_a_blk))
        a_neg = to_int(cell_val(cells, i_a_neg))
        a_pos = to_int(cell_val(cells, i_a_pos))
        a_pts = to_int(cell_val(cells, i_a_pts))
        a_tot = a_err + a_ok + a_blk + a_neg + a_pos + a_pts
        atk_pct = to_float(cell_val(cells, i_a_pct)) or (round(100.0 * a_pts / a_tot, 1) if a_tot > 0 else 0.0)
        atk_eff_pct = round(100.0 * (a_pts - a_err - a_blk) / a_tot, 1) if a_tot > 0 else 0.0

        page_players[pl_id] = {
            "pl_id": pl_id,
            "url": f"{base}/PlayerDetails.aspx?ID={cid}&PlayerID={pl_id}",
            "name": full_name,
            "team": cell_val(cells, i_team),
            "country": cell_val(cells, i_country),
            "matches": to_int(cell_val(cells, i_matches)),
            "sets": to_int(cell_val(cells, i_sets)),
            "points": to_int(cell_val(cells, i_points)),
            "break_points": to_int(cell_val(cells, i_bp)),
            "won_lost": to_int(cell_val(cells, i_wl)),
            "serve_total": s_tot,
            "aces": aces,
            "serve_errors": s_err,
            "reception_total": r_tot,
            "reception_pos_pct": rec_pos_pct,
            "reception_perf_pct": rec_perf_pct,
            "attack_total": a_tot,
            "attack_points": a_pts,
            "attack_errors": a_err,
            "attack_blocked": a_blk,
            "attack_pct": atk_pct,
            "attack_eff_pct": atk_eff_pct,
            "blocks": to_int(cell_val(cells, i_b_pts)),
        }

    return page_players


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
                    """() => Array.from(document.querySelectorAll('tr.rgRow, tr.rgAltRow')).some(r => (r.textContent || '').trim().length > 10)""",
                    timeout=18000,
                )
            except Exception:
                continue

            prev_sig = None
            for _ in range(60):
                batch = parse_radgrid_page(BeautifulSoup(page.content(), "lxml"), league)
                if not batch:
                    break
                curr_sig = tuple(list(batch.keys())[:3])
                if curr_sig == prev_sig:
                    break
                prev_sig = curr_sig

                new_count = sum(1 for k in batch if k not in all_players)
                all_players.update(batch)
                if new_count == 0:
                    break

                prev_first = page.evaluate("() => { const r = document.querySelector('tr.rgRow'); return r ? r.textContent.trim() : ''; }")
                status = page.evaluate(
                    """() => {
                        if (typeof $find !== 'function') return 'no_find';
                        for (const g of document.querySelectorAll('.RadGrid')) {
                            const ctrl = g.id ? $find(g.id) : null;
                            if (ctrl && typeof ctrl.get_masterTableView === 'function') {
                                const mtv = ctrl.get_masterTableView();
                                if (mtv.get_pageCount() > 0 && mtv.get_currentPageIndex() >= mtv.get_pageCount() - 1) return 'last';
                                mtv.page('Next');
                                return 'paged';
                            }
                        }
                        return 'no_grid';
                    }"""
                )
                if status == "last":
                    break
                if status != "paged":
                    btn = page.locator("input.rgPageNext, button.rgPageNext, a.rgPageNext, [title='Next Page']").first
                    if btn.count() == 0 or btn.evaluate("el => Boolean(el.disabled || el.classList.contains('rgDisabled'))"):
                        break
                    btn.click(force=True)

                try:
                    page.wait_for_function(
                        """(prev) => { const r = document.querySelector('tr.rgRow'); const c = r ? r.textContent.trim() : ''; return c.length > 10 && c !== prev; }""",
                        arg=prev_first,
                        timeout=12000,
                    )
                except Exception:
                    break

            if all_players:
                print(f"[{league['name']}] Browser scraped {len(all_players)} players with full stats.")
                break
    finally:
        context.close()

    return all_players


def discover_extra_players_from_teams(session, league, existing_players):
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", "")).strip()

    seed_urls = [
        f"{base}/CompetitionHome.aspx?ID={cid}",
        f"{base}/CompetitionTeamSearch.aspx?ID={cid}",
        f"{base}/CompetitionPlayerSearch.aspx?ID={cid}",
    ]
    if pid:
        seed_urls.append(f"{base}/CompetitionStandings.aspx?ID={cid}&PID={pid}")

    team_urls = set()
    player_urls = {}

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
        bio = {}
        name_guess = ""

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
        "league": league["name"],
        "country": league["country"],
        "gender": league.get("gender", ""),
        "name": name or f"Player {pl_id}",
        "team": team,
        "position": normalize_position(raw_pos),
        "raw_position": raw_pos,
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
                print(f"=== Processing {league['name']} ===")
                if league.get("type") == "cvf_cz":
                    all_records.extend(scrape_cvf_cz_league(browser, session, league))
                    continue

                cand_urls = resolve_dataproject_stats_urls(session, league)
                players = scrape_all_players_playwright(browser, league, cand_urls)
                players = discover_extra_players_from_teams(session, league, players)

                with ThreadPoolExecutor(max_workers=12) as executor:
                    futures = [executor.submit(enrich_player_profile, league, pl_id, pdata) for pl_id, pdata in players.items()]
                    for fut in as_completed(futures):
                        rec = fut.result()
                        if rec:
                            all_records.append(rec)
        finally:
            browser.close()

    os.makedirs("data", exist_ok=True)
    df = pd.DataFrame(all_records)
    if not df.empty:
        df = df.drop_duplicates(subset=["player_id"], keep="first")
        df = apply_manual_overrides(df)
        df = df.sort_values(by=["points", "name"], ascending=[False, True])

    df.to_csv("data/players.csv", index=False, encoding="utf-8-sig")

    meta = {
        "last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_players": len(df),
        "leagues": [l["name"] for l in leagues],
    }
    with open("data/last_updated.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"=== DONE: Saved {len(df)} players across {len(leagues)} leagues to data/players.csv ===")


if __name__ == "__main__":
    main()
