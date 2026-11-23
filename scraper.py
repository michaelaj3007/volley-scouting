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
    i_r_pos = find_col(headers, ["r +", "r+"])
    i_r_perf = find_col(headers, ["r #", "r#"])

    i_a_err = find_col(headers, ["a =", "a=", "a err"])
    i_a_ok = find_col(headers, ["a !", "a!"])
    i_a_blk = find_col(headers, ["a /", "a/", "a blk"])
    i_a_neg = find_col(headers, ["a -", "a-"])
    i_a_pos = find_col(headers, ["a +", "a+"])
    i_a_pts = find_col(headers, ["a #", "a#", "a pts", "atk pts"])

    i_b_err = find_col(headers, ["b =", "b="])
    i_b_pts = find_col(headers, ["b #", "b#", "b pts", "blk", "blocks", "block"])

    r_start = i_r_perf if i_r_perf is not None else 0
    r_end = i_a_err if i_a_err is not None else len(headers)
    i_r_pos_pct = find_col(headers, ["pos%", "r pos%", "rec pos%"], r_start, r_end)
    i_r_perf_pct = find_col(headers, ["exc.%", "exc%", "perf%", "r perf%"], r_start, r_end)

    a_start = i_a_pts if i_a_pts is not None else 0
    a_end = i_b_err if i_b_err is not None else len(headers)
    i_a_pct = find_col(headers, ["exc. %", "exc.%", "exc%", "a %", "atk %"], a_start, a_end)

    data_rows = soup.select("tr.rgRow, tr.rgAltRow")
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

        first_n = cell_val(cells, i_name)
        last_n = cell_val(cells, i_surname)
        raw_player = cell_val(cells, i_player)
        if last_n or first_n:
            full_name = f"{last_n} {first_n}".strip()
        else:
            full_name = re.sub(r"\s*\([^)]*\)\s*$", "", raw_player).strip()

        matches = to_int(cell_val(cells, i_matches))
        sets = to_int(cell_val(cells, i_sets))
        points = to_int(cell_val(cells, i_points))
        bp = to_int(cell_val(cells, i_bp))
        wl = to_int(cell_val(cells, i_wl))

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
        rec_pos_pct = to_float(cell_val(cells, i_r_pos_pct))
        if rec_pos_pct == 0.0 and r_tot > 0:
            rec_pos_pct = round(100.0 * (r_pos + r_perf) / r_tot, 1)
        rec_perf_pct = to_float(cell_val(cells, i_r_perf_pct))
        if rec_perf_pct == 0.0 and r_tot > 0:
            rec_perf_pct = round(100.0 * r_perf / r_tot, 1)

        a_err = to_int(cell_val(cells, i_a_err))
        a_ok = to_int(cell_val(cells, i_a_ok))
        a_blk = to_int(cell_val(cells, i_a_blk))
        a_neg = to_int(cell_val(cells, i_a_neg))
        a_pos = to_int(cell_val(cells, i_a_pos))
        a_pts = to_int(cell_val(cells, i_a_pts))
        a_tot = a_err + a_ok + a_blk + a_neg + a_pos + a_pts
        atk_pct = to_float(cell_val(cells, i_a_pct))
        if atk_pct == 0.0 and a_tot > 0:
            atk_pct = round(100.0 * a_pts / a_tot, 1)
        atk_eff_pct = round(100.0 * (a_pts - a_err - a_blk) / a_tot, 1) if a_tot > 0 else 0.0

        blocks = to_int(cell_val(cells, i_b_pts))

        page_players[pl_id] = {
            "pl_id": pl_id,
            "url": f"{base}/PlayerDetails.aspx?ID={cid}&PlayerID={pl_id}",
            "name": full_name,
            "team": cell_val(cells, i_team),
            "country": cell_val(cells, i_country),
            "matches": matches,
            "sets": sets,
            "points": points,
            "break_points": bp,
            "won_lost": wl,
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
            "blocks": blocks,
        }

    return page_players, len(headers)


def scrape_all_players_playwright(browser, league, candidate_urls):
    all_players = {}
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=HEADERS["User-Agent"],
    )
    page = context.new_page()

    try:
        for url in candidate_urls:
            print(f"[{league['name']}] Opening browser on {url}")
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
            except Exception as exc:
                print(f"[WARN] Navigation error on {url}: {exc}")
                continue

            try:
                page.wait_for_function(
                    """() => {
                        const rows = document.querySelectorAll('tr.rgRow, tr.rgAltRow');
                        for (const r of rows) {
                            if ((r.textContent || '').trim().length > 10) return true;
                        }
                        return false;
                    }""",
                    timeout=18000,
                )
            except Exception:
                print(f"[WARN] No populated RadGrid rows on {url}")
                continue

            prev_sig = None
            for page_num in range(1, 60):
                soup = BeautifulSoup(page.content(), "lxml")
                batch, _ = parse_radgrid_page(soup, league)
                if not batch:
                    break

                curr_sig = tuple(list(batch.keys())[:3])
                if curr_sig == prev_sig:
                    break
                prev_sig = curr_sig

                new_count = 0
                for pl_id, rec in batch.items():
                    if pl_id not in all_players:
                        new_count += 1
                    all_players[pl_id] = rec

                if new_count == 0:
                    break

                prev_first_text = page.evaluate(
                    "() => { const r = document.querySelector('tr.rgRow'); return r ? (r.textContent || '').trim() : ''; }"
                )

                advanced_via_api = page.evaluate(
                    """() => {
                        if (typeof $find !== 'function') return 'no_find';
                        const grids = document.querySelectorAll('.RadGrid');
                        for (const g of grids) {
                            if (!g.id) continue;
                            const ctrl = $find(g.id);
                            if (ctrl && typeof ctrl.get_masterTableView === 'function') {
                                const mtv = ctrl.get_masterTableView();
                                const idx = mtv.get_currentPageIndex();
                                const total = mtv.get_pageCount();
                                if (total > 0 && idx >= total - 1) return 'last_page';
                                mtv.page('Next');
                                return 'paged';
                            }
                        }
                        return 'no_grid';
                    }"""
                )

                if advanced_via_api == "last_page":
                    break

                if advanced_via_api != "paged":
                    next_btn = page.locator(
                        "input.rgPageNext, button.rgPageNext, a.rgPageNext, [title='Next Page']"
                    ).first
                    if next_btn.count() == 0:
                        break
                    is_disabled = next_btn.evaluate(
                        "el => Boolean(el.disabled || el.classList.contains('rgDisabled'))"
                    )
                    if is_disabled:
                        break
                    next_btn.scroll_into_view_if_needed()
                    next_btn.click(force=True)

                try:
                    page.wait_for_function(
                        """(prevText) => {
                            const r = document.querySelector('tr.rgRow');
                            const cur = r ? (r.textContent || '').trim() : '';
                            return cur.length > 10 && cur !== prevText;
                        }""",
                        arg=prev_first_text,
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

    def register_links(raw_html):
        text = html.unescape(raw_html)
        for m in re.findall(r"CompetitionTeamDetails\.aspx\?[^\"'<>\s)]+", text, re.IGNORECASE):
            team_urls.add(urljoin(base + "/", m))
        for m in re.findall(r"PlayerDetails\.aspx\?[^\"'<>\s)]+", text, re.IGNORECASE):
            full = urljoin(base + "/", m)
            pl_id = extract_query_param(full, "PlayerID")
            if not pl_id:
                continue
            if pl_id not in player_urls or ("teamid=" in full.lower() and "teamid=" not in player_urls[pl_id].lower()):
                player_urls[pl_id] = full
        for pl_id in re.findall(r"PlayerID=(\d+)", text, re.IGNORECASE):
            if pl_id not in player_urls:
                player_urls[pl_id] = f"{base}/PlayerDetails.aspx?ID={cid}&PlayerID={pl_id}"

    for url in seed_urls:
        raw = fetch_html(session, url)
        if raw:
            register_links(raw)

    for t_url in sorted(team_urls):
        raw = fetch_html(session, t_url)
        if raw:
            register_links(raw)

    added = 0
    for pl_id, p_url in player_urls.items():
        if pl_id in existing_players:
            if "teamid=" in p_url.lower():
                existing_players[pl_id]["url"] = p_url
        else:
            existing_players[pl_id] = {
                "pl_id": pl_id,
                "url": p_url,
                "name": "",
                "team": "",
                "country": "",
                "matches": 0,
                "sets": 0,
                "points": 0,
                "break_points": 0,
                "won_lost": 0,
                "serve_total": 0,
                "aces": 0,
                "serve_errors": 0,
                "reception_total": 0,
                "reception_pos_pct": 0.0,
                "reception_perf_pct": 0.0,
                "attack_total": 0,
                "attack_points": 0,
                "attack_errors": 0,
                "attack_blocked": 0,
                "attack_pct": 0.0,
                "attack_eff_pct": 0.0,
                "blocks": 0,
            }
            added += 1

    print(f"[{league['name']}] Found {len(team_urls)} teams, added {added} extra roster players.")
    return existing_players


def parse_bio_and_name_from_lines(soup):
    lines = [clean_text(x) for x in soup.get_text("\n").splitlines()]
    lines = [l for l in lines if l]
    bio = {}
    name_guess = ""

    for i, line in enumerate(lines):
        low = line.rstrip(":").lower()
        if low in ("shirt number", "número", "αριθμός") and i > 0 and not name_guess:
            for back in range(1, min(4, i + 1)):
                cand = re.sub(r"^\d+\s+", "", lines[i - back]).strip()
                c_low = cand.lower()
                if (
                    cand
                    and len(cand) >= 3
                    and c_low not in KNOWN_BIO_LABELS
                    and not any(bad in c_low for bad in ["image ", "select", "statistics", "home", "campionat"])
                ):
                    name_guess = cand
                    break

        if i < len(lines) - 1 and low in KNOWN_BIO_LABELS:
            nxt = lines[i + 1]
            nxt_low = nxt.rstrip(":").lower()
            if nxt_low not in KNOWN_BIO_LABELS and nxt not in ("-", "—", "N/A"):
                bio.setdefault(low, nxt)

    return bio, name_guess


def enrich_player_profile(league, pl_id, data):
    session = get_session()
    raw_html = fetch_html(session, data["url"])

    name = data["name"]
    team = data["team"]
    nationality = data["country"]
    raw_pos = ""
    height_cm = 0
    birth_year = 0
    age = 0
    photo_url = ""

    if raw_html:
        soup = BeautifulSoup(raw_html, "lxml")
        bio, name_guess = parse_bio_and_name_from_lines(soup)

        if not name:
            el_sur = soup.find(id=re.compile(r"LBL_Surname|PlayerSurname", re.IGNORECASE))
            el_nam = soup.find(id=re.compile(r"LBL_Name|PlayerName", re.IGNORECASE))
            if el_sur or el_nam:
                s_txt = clean_text(el_sur.get_text()) if el_sur else ""
                n_txt = clean_text(el_nam.get_text()) if el_nam else ""
                name = f"{s_txt} {n_txt}".strip()
            if not name:
                name = name_guess

        if not team:
            team = bio.get("club") or bio.get("team") or ""

        raw_pos = bio.get("position") or bio.get("role") or ""

        raw_height = (
            bio.get("heigth")
            or bio.get("height")
            or bio.get("înălțime")
            or bio.get("inaltime")
            or bio.get("ύψος")
            or bio.get("výška")
            or ""
        )
        h_val = to_int(raw_height)
        if 140 <= h_val <= 235:
            height_cm = h_val

        raw_birth = bio.get("date of birth") or bio.get("birth date") or bio.get("ημ. γέννησης") or ""
        ym = re.search(r"(19\d{2}|20[0-1]\d)", raw_birth)
        if ym:
            birth_year = int(ym.group(1))
            age = datetime.now().year - birth_year

        if not nationality:
            nationality = bio.get("sport nationality") or bio.get("nationality") or bio.get("εθνικότητα") or ""

        img = soup.find("img", id=re.compile(r"player|photo|img", re.IGNORECASE))
        if img and img.get("src"):
            photo_url = urljoin(league["base_url"].rstrip("/") + "/", img["src"])

    sets = data["sets"]
    pts_per_set = round(data["points"] / sets, 2) if sets > 0 else 0.0
    aces_per_set = round(data["aces"] / sets, 2) if sets > 0 else 0.0
    blocks_per_set = round(data["blocks"] / sets, 2) if sets > 0 else 0.0

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
        "points_per_set": pts_per_set,
        "break_points": data["break_points"],
        "won_lost": data["won_lost"],
        "serve_total": data["serve_total"],
        "aces": data["aces"],
        "aces_per_set": aces_per_set,
        "serve_errors": data["serve_errors"],
        "blocks": data["blocks"],
        "blocks_per_set": blocks_per_set,
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


def enrich_cvf_player_bio(session, rec):
    p_url = rec.get("profile_url", "")
    if not p_url or "/hrac" not in p_url.lower():
        return rec

    raw_html = fetch_html(session, p_url)
    if not raw_html:
        return rec

    soup = BeautifulSoup(raw_html, "lxml")
    bio, _ = parse_bio_and_name_from_lines(soup)

    if not rec["raw_position"]:
        raw_pos = bio.get("post") or bio.get("position") or bio.get("role") or ""
        if raw_pos:
            rec["raw_position"] = raw_pos
            rec["position"] = normalize_position(raw_pos)

    if rec["height_cm"] == 0:
        h_raw = bio.get("výška") or bio.get("vyska") or bio.get("height") or ""
        h_val = to_int(h_raw)
        if 140 <= h_val <= 235:
            rec["height_cm"] = h_val

    if rec["birth_year"] == 0:
        b_raw = bio.get("datum narození") or bio.get("ročník") or bio.get("birth date") or ""
        ym = re.search(r"(19\d{2}|20[0-1]\d)", b_raw)
        if ym:
            rec["birth_year"] = int(ym.group(1))
            rec["age"] = datetime.now().year - rec["birth_year"]

    nat = bio.get("státní příslušnost") or bio.get("nationality") or ""
    if nat:
        rec["nationality"] = nat

    return rec


def get_table_context_title(table):
    parts = []
    prev = table.find_previous(["h1", "h2", "h3", "h4", "caption"])
    if prev:
        parts.append(normalize_header(prev.get_text()))
    card = table.find_parent(class_=re.compile(r"card|panel|box|block|section|tab-pane", re.IGNORECASE))
    if card:
        hdr = card.find(class_=re.compile(r"header|title|heading", re.IGNORECASE))
        if hdr:
            parts.append(normalize_header(hdr.get_text()))
    return " | ".join(dict.fromkeys(p for p in parts if p))


def parse_cvf_table(table, league, page_url):
    base = league["base_url"].rstrip("/")
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    ctx_title = get_table_context_title(table)

    header_row_idx = None
    headers = []
    stat_keywords = [
        "hráč", "hrac", "příjmení", "prijmeni", "jméno", "jmeno", "player",
        "klub", "družstvo", "druzstvo", "tým", "tym", "sety", "body", "esa",
        "bloky", "blok", "útok", "utok", "příjem", "prijem", "záp", "zap", "poř", "por",
    ]
    for r_i, tr in enumerate(rows[:4]):
        h_cells = [normalize_header(c.get_text()) for c in tr.find_all(["th", "td"])]
        joined = " ".join(h_cells)
        if len(h_cells) >= 3 and any(k in joined for k in stat_keywords):
            header_row_idx = r_i
            headers = h_cells
            break

    data_start_idx = (header_row_idx + 1) if header_row_idx is not None else 0
    if header_row_idx is None:
        # Check if rows contain player links (/hrac) even without a recognizable header row
        has_player_links = any(
            tr.find("a", href=re.compile(r"/hrac|player", re.IGNORECASE))
            for tr in rows[:6]
        )
        if not has_player_links:
            return []
        first_cells = [clean_text(c.get_text()) for c in rows[0].find_all(["td", "th"])]
        headers = [f"col_{i}" for i in range(len(first_cells))]

    group_map = {}
    if header_row_idx is not None and header_row_idx > 0:
        col_cursor = 0
        for cell in rows[0].find_all(["th", "td"]):
            span = int(cell.get("colspan", 1))
            g_txt = normalize_header(cell.get_text())
            for offset in range(span):
                group_map[col_cursor + offset] = g_txt
            col_cursor += span

    print(f"[DEBUG][CVF] Title='{ctx_title}' | Headers={headers}")

    col_map = {h: i for i, h in enumerate(headers) if h}

    i_name = find_col(col_map, ["hráč", "hrac", "player", "příjmení a jméno", "prijmeni a jmeno"])
    i_surname = find_col(col_map, ["příjmení", "prijmeni", "surname"])
    i_firstname = find_col(col_map, ["jméno", "jmeno", "name"])
    i_team = find_col(col_map, ["družstvo", "druzstvo", "tým", "tym", "klub", "team"])
    i_pos = find_col(col_map, ["post", "pozice", "role", "position"])
    i_height = find_col(col_map, ["výška", "vyska", "height"])
    i_birth = find_col(col_map, ["ročník", "rocnik", "nar.", "narození", "datum narození", "birth"])
    i_matches = find_col(col_map, ["záp.", "zap.", "z", "utk.", "utkání", "matches"])
    i_sets = find_col(col_map, ["sety", "s", "sets"])

    # Auto-infer player name & team columns from <a> links when headers are blank
    sample_trs = rows[data_start_idx : data_start_idx + 5]
    if i_name is None and i_surname is None:
        link_col_hits = {}
        text_col_hits = {}
        for tr in sample_trs:
            tds = tr.find_all(["td", "th"])
            for c_i, td in enumerate(tds):
                if c_i == i_team:
                    continue
                txt = clean_text(td.get_text())
                if not txt or re.fullmatch(r"[\d.,%+\- ]+", txt):
                    continue
                a = td.find("a", href=True)
                if a and ("/hrac" in a["href"].lower() or "player" in a["href"].lower()):
                    link_col_hits[c_i] = link_col_hits.get(c_i, 0) + 1
                elif len(txt) >= 4:
                    text_col_hits[c_i] = text_col_hits.get(c_i, 0) + 1
        if link_col_hits:
            i_name = max(link_col_hits, key=link_col_hits.get)
        elif text_col_hits:
            i_name = sorted(text_col_hits.keys())[0]

    if i_team is None:
        for tr in sample_trs:
            for c_i, td in enumerate(tr.find_all(["td", "th"])):
                if c_i == i_name:
                    continue
                a = td.find("a", href=True)
                if a and any(k in a["href"].lower() for k in ["/druzstvo", "/tym", "/klub", "team"]):
                    i_team = c_i
                    break
            if i_team is not None:
                break

    is_block_table = "blok" in ctx_title and not group_map
    is_serve_table = ("podán" in ctx_title or "podan" in ctx_title or "esa" in ctx_title) and not group_map
    is_attack_table = ("útok" in ctx_title or "utok" in ctx_title or "smeč" in ctx_title) and not group_map
    is_rec_table = ("příj" in ctx_title or "prij" in ctx_title) and not group_map

    i_pts = None if (is_block_table or is_serve_table or is_attack_table or is_rec_table) else find_col(
        col_map, ["body", "b", "pts", "points", "celkem"]
    )

    i_aces = find_col(col_map, ["esa", "eso", "podání #", "podani #", "aces"])
    i_s_tot = find_col(col_map, ["podání celk", "podani celk", "podání", "podani"])
    i_s_err = find_col(col_map, ["podání =", "podani ="])

    i_blk = find_col(col_map, ["bloky", "blok", "blok #", "blocks"])

    i_atk_pts = find_col(col_map, ["útok #", "utok #", "útok body", "utok body"])
    i_atk_tot = find_col(col_map, ["útok celk", "utok celk", "útok", "utok"])
    i_atk_pct = find_col(col_map, ["útok %", "utok %", "úsp. útoku", "usp. utoku", "úsp.%", "usp.%"])

    i_rec_tot = find_col(col_map, ["příjem celk", "prijem celk", "příjem", "prijem"])
    i_rec_pct = find_col(col_map, ["příjem %", "prijem %", "poz. příjem", "poz. prijem", "poz.%"])
    i_rec_perf = find_col(col_map, ["perf. příjem", "perf. prijem", "výb.%", "vyb.%"])

    if is_block_table and i_blk is None:
        i_blk = find_col(col_map, ["body", "b", "#", "celkem"])
    if is_serve_table and i_aces is None:
        i_aces = find_col(col_map, ["body", "b", "#"])
    if is_attack_table:
        if i_atk_pts is None:
            i_atk_pts = find_col(col_map, ["body", "b", "#"])
        if i_atk_pct is None:
            i_atk_pct = find_col(col_map, ["%", "úsp%", "úsp.%"])
    if is_rec_table and i_rec_pct is None:
        i_rec_pct = find_col(col_map, ["%", "poz%", "poz.%"])

    if group_map:
        for idx, h in enumerate(headers):
            grp = group_map.get(idx, "")
            if any(k in grp for k in ["podán", "podan", "serv"]):
                if h in ("#", "esa", "eso", "body", "b") and i_aces is None:
                    i_aces = idx
                elif h in ("celk", "celkem", "tot") and i_s_tot is None:
                    i_s_tot = idx
                elif h in ("=", "ch", "chyby") and i_s_err is None:
                    i_s_err = idx
            elif any(k in grp for k in ["příj", "prij", "recep"]):
                if h in ("celk", "celkem", "tot") and i_rec_tot is None:
                    i_rec_tot = idx
                elif h in ("%", "poz%", "poz.%") and i_rec_pct is None:
                    i_rec_pct = idx
                elif h in ("#", "perf%", "výb%", "vyb.%") and i_rec_perf is None:
                    i_rec_perf = idx
            elif any(k in grp for k in ["útok", "utok", "att"]):
                if h in ("#", "body", "b") and i_atk_pts is None:
                    i_atk_pts = idx
                elif h in ("celk", "celkem", "tot") and i_atk_tot is None:
                    i_atk_tot = idx
                elif h in ("%", "úsp%", "úsp.%") and i_atk_pct is None:
                    i_atk_pct = idx
            elif any(k in grp for k in ["blok", "block"]):
                if h in ("#", "body", "b", "celk", "celkem") and i_blk is None:
                    i_blk = idx

    records = []
    for tr in rows[data_start_idx:]:
        cells = [clean_text(c.get_text()) for c in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue

        if i_surname is not None and i_firstname is not None:
            p_name = f"{cell_val(cells, i_surname)} {cell_val(cells, i_firstname)}".strip()
        elif i_name is not None:
            p_name = cell_val(cells, i_name)
        elif i_surname is not None:
            p_name = cell_val(cells, i_surname)
        else:
            p_name = ""

        if not p_name or p_name.lower() in ("celkem", "total", "hráč", "hrac", "příjmení", "prijmeni"):
            continue

        team_name = cell_val(cells, i_team)
        link = tr.find("a", href=re.compile(r"/hrac|player", re.IGNORECASE)) or tr.find("a", href=True)
        p_url = urljoin(base + "/", link["href"]) if link else page_url
        pl_id_match = re.search(r"(\d{2,})", p_url)
        pl_id = pl_id_match.group(1) if pl_id_match else re.sub(r"\W+", "_", f"{p_name}_{team
