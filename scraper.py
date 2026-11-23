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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ro;q=0.8",
}

ROLE_MAP = {
    "setter": "Setter",
    "ridicator": "Setter",
    "ridicător": "Setter",
    "palleggiatore": "Setter",
    "πασαδόρος": "Setter",
    "opposite": "Opposite",
    "universal": "Opposite",
    "fals": "Opposite",
    "opposto": "Opposite",
    "διαγώνιος": "Opposite",
    "outside": "Outside Hitter",
    "wing": "Outside Hitter",
    "spiker": "Outside Hitter",
    "extremă": "Outside Hitter",
    "extrema": "Outside Hitter",
    "secund": "Outside Hitter",
    "schiacciatore": "Outside Hitter",
    "ακραίος": "Outside Hitter",
    "middle": "Middle Blocker",
    "centru": "Middle Blocker",
    "centrale": "Middle Blocker",
    "κεντρικός": "Middle Blocker",
    "libero": "Libero",
    "λίμπερο": "Libero",
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


def find_col(col_map, exact_candidates, contains_candidates=None):
    for cand in exact_candidates:
        c_norm = normalize_header(cand)
        if c_norm in col_map:
            return col_map[c_norm]
        c_nospace = c_norm.replace(" ", "")
        for h, idx in col_map.items():
            if h.replace(" ", "") == c_nospace:
                return idx
    if contains_candidates:
        for cand in contains_candidates:
            c_norm = normalize_header(cand)
            for h, idx in col_map.items():
                if c_norm in h:
                    return idx
    return None


def cell_val(cells, idx, default=""):
    if idx is not None and 0 <= idx < len(cells):
        return cells[idx]
    return default


def scrape_all_players_table(session, league):
    """Reads the complete Data Project Statistics_AllPlayers.aspx master table."""
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", ""))

    urls = []
    if pid:
        urls.append(f"{base}/Statistics_AllPlayers.aspx?ID={cid}&PID={pid}")
    urls.append(f"{base}/Statistics_AllPlayers.aspx?ID={cid}")

    players = {}

    for url in urls:
        raw_html = fetch_html(session, url)
        if not raw_html:
            continue
        soup = BeautifulSoup(raw_html, "lxml")

        target_table = None
        header_row_idx = 0
        headers = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            for r_i, tr in enumerate(rows[:4]):
                h_cells = [normalize_header(c.get_text()) for c in tr.find_all(["th", "td"])]
                if "playerid" in h_cells or ("player" in h_cells and "points" in h_cells):
                    target_table = table
                    header_row_idx = r_i
                    headers = h_cells
                    break
            if target_table:
                break

        if not target_table:
            continue

        print(f"[DEBUG] AllPlayers FULL headers ({len(headers)} cols): {headers}")

        col_map = {}
        for i, h in enumerate(headers):
            if h and h not in col_map:
                col_map[h] = i

        i_pid = find_col(col_map, ["playerid", "player id", "id"])
        i_player = find_col(col_map, ["player", "jucător", "jucator"])
        i_surname = find_col(col_map, ["surname", "last name"])
        i_name = find_col(col_map, ["name", "first name"])
        i_team = find_col(col_map, ["team", "echipa", "club"])
        i_country = find_col(col_map, ["country", "nation", "nat"])
        i_matches = find_col(col_map, ["matches", "meciuri", "played", "m"])
        i_sets = find_col(col_map, ["sets", "seturi", "s"])
        i_points = find_col(col_map, ["points", "puncte", "pts", "tot"])
        i_bp = find_col(col_map, ["bp", "break points"])
        i_wl = find_col(col_map, ["w-p", "w-l", "wl"])

        # Serve columns (DataVolley notation: s =, s !, s /, s -, s +, s #, s tot)
        i_s_err = find_col(col_map, ["s =", "s=", "s err", "srv err"])
        i_s_ok = find_col(col_map, ["s !", "s!"])
        i_s_slash = find_col(col_map, ["s /", "s/"])
        i_s_neg = find_col(col_map, ["s -", "s-"])
        i_s_pos = find_col(col_map, ["s +", "s+"])
        i_s_ace = find_col(col_map, ["s #", "s#", "s pts", "aces", "ace", "srv pts"])
        i_s_tot = find_col(col_map, ["s tot", "stot", "srv tot"])

        # Reception columns (r =, r /, r -, r !, r +, r #, r tot)
        i_r_err = find_col(col_map, ["r =", "r=", "r err"])
        i_r_slash = find_col(col_map, ["r /", "r/"])
        i_r_neg = find_col(col_map, ["r -", "r-"])
        i_r_ok = find_col(col_map, ["r !", "r!"])
        i_r_pos = find_col(col_map, ["r +", "r+"])
        i_r_perf = find_col(col_map, ["r #", "r#"])
        i_r_tot = find_col(col_map, ["r tot", "rtot", "rec tot"])
        i_r_pos_pct = find_col(col_map, ["r pos%", "pos%", "rec pos%", "r %"])
        i_r_perf_pct = find_col(col_map, ["r perf%", "r exc%", "perf%", "exc%"])

        # Attack columns (a =, a /, a -, a !, a +, a #, a tot, a %)
        i_a_err = find_col(col_map, ["a =", "a=", "a err"])
        i_a_blk = find_col(col_map, ["a /", "a/", "a blk"])
        i_a_neg = find_col(col_map, ["a -", "a-"])
        i_a_ok = find_col(col_map, ["a !", "a!"])
        i_a_pos = find_col(col_map, ["a +", "a+"])
        i_a_pts = find_col(col_map, ["a #", "a#", "a pts", "atk pts", "attack pts"])
        i_a_tot = find_col(col_map, ["a tot", "atot", "atk tot"])
        i_a_pct = find_col(col_map, ["a %", "a%", "a exc%", "atk %", "attack %"])

        # Block columns (b =, b /, b -, b !, b +, b #, b pts)
        i_b_pts = find_col(col_map, ["b #", "b#", "b pts", "blk", "blocks", "block", "bl"])

        data_rows = target_table.find_all("tr")[header_row_idx + 1 :]
        sample_printed = False

        for tr in data_rows:
            cells = [clean_text(c.get_text()) for c in tr.find_all(["td", "th"])]
            if len(cells) < max(5, len(headers) // 2):
                continue

            pl_id = re.sub(r"[^0-9]", "", cell_val(cells, i_pid))
            if not pl_id:
                link = tr.find("a", href=re.compile(r"PlayerID=", re.IGNORECASE))
                if link:
                    pl_id = extract_query_param(urljoin(base + "/", link["href"]), "PlayerID")
            if not pl_id:
                continue

            if not sample_printed:
                print(f"[DEBUG] Sample AllPlayers row: {cells[:25]}")
                sample_printed = True

            first_n = cell_val(cells, i_name)
            last_n = cell_val(cells, i_surname)
            full_name = f"{first_n} {last_n}".strip() if (first_n or last_n) else cell_val(cells, i_player)

            matches = to_int(cell_val(cells, i_matches))
            sets = to_int(cell_val(cells, i_sets))
            points = to_int(cell_val(cells, i_points))
            bp = to_int(cell_val(cells, i_bp))
            wl = to_int(cell_val(cells, i_wl))

            # Serve
            s_err = to_int(cell_val(cells, i_s_err))
            s_ok = to_int(cell_val(cells, i_s_ok))
            s_slash = to_int(cell_val(cells, i_s_slash))
            s_neg = to_int(cell_val(cells, i_s_neg))
            s_pos = to_int(cell_val(cells, i_s_pos))
            aces = to_int(cell_val(cells, i_s_ace))
            s_tot = to_int(cell_val(cells, i_s_tot))
            if s_tot == 0:
                s_tot = s_err + s_ok + s_slash + s_neg + s_pos + aces

            # Reception
            r_err = to_int(cell_val(cells, i_r_err))
            r_slash = to_int(cell_val(cells, i_r_slash))
            r_neg = to_int(cell_val(cells, i_r_neg))
            r_ok = to_int(cell_val(cells, i_r_ok))
            r_pos = to_int(cell_val(cells, i_r_pos))
            r_perf = to_int(cell_val(cells, i_r_perf))
            r_tot = to_int(cell_val(cells, i_r_tot))
            if r_tot == 0:
                r_tot = r_err + r_slash + r_neg + r_ok + r_pos + r_perf
            rec_pos_pct = to_float(cell_val(cells, i_r_pos_pct))
            if rec_pos_pct == 0.0 and r_tot > 0:
                rec_pos_pct = round(100.0 * (r_pos + r_perf) / r_tot, 1)
            rec_perf_pct = to_float(cell_val(cells, i_r_perf_pct))
            if rec_perf_pct == 0.0 and r_tot > 0:
                rec_perf_pct = round(100.0 * r_perf / r_tot, 1)

            # Attack
            a_err = to_int(cell_val(cells, i_a_err))
            a_blk = to_int(cell_val(cells, i_a_blk))
            a_neg = to_int(cell_val(cells, i_a_neg))
            a_ok = to_int(cell_val(cells, i_a_ok))
            a_pos = to_int(cell_val(cells, i_a_pos))
            a_pts = to_int(cell_val(cells, i_a_pts))
            a_tot = to_int(cell_val(cells, i_a_tot))
            if a_tot == 0:
                a_tot = a_err + a_blk + a_neg + a_ok + a_pos + a_pts
            atk_pct = to_float(cell_val(cells, i_a_pct))
            if atk_pct == 0.0 and a_tot > 0:
                atk_pct = round(100.0 * a_pts / a_tot, 1)
            atk_eff_pct = round(100.0 * (a_pts - a_err - a_blk) / a_tot, 1) if a_tot > 0 else 0.0

            # Block
            blocks = to_int(cell_val(cells, i_b_pts))

            players[pl_id] = {
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

        if players:
            print(f"[{league['name']}] Loaded {len(players)} players from {url}")
            break

    return players


def discover_extra_players_from_teams(session, league, existing_players):
    """Also scans team rosters (including onclick JS) to catch players with 0 sets played."""
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", ""))

    seed_urls = [
        f"{base}/CompetitionTeamSearch.aspx?ID={cid}",
        f"{base}/CompetitionPlayerSearch.aspx?ID={cid}",
    ]
    if pid:
        seed_urls.append(f"{base}/CompetitionStandings.aspx?ID={cid}&PID={pid}")

    team_urls = set()
    found_ids = set()

    for url in seed_urls:
        raw_html = html.unescape(fetch_html(session, url))
        if not raw_html:
            continue
        for m in re.findall(r"CompetitionTeamDetails\.aspx\?[^\"'<>\s]+", raw_html, re.IGNORECASE):
            team_urls.add(urljoin(base + "/", m))
        for m in re.findall(r"PlayerID=(\d+)", raw_html, re.IGNORECASE):
            found_ids.add(m)

    for t_url in sorted(team_urls):
        raw_html = html.unescape(fetch_html(session, t_url))
        if not raw_html:
            continue
        for m in re.findall(r"PlayerID=(\d+)", raw_html, re.IGNORECASE):
            found_ids.add(m)

    added = 0
    for pl_id in found_ids:
        if pl_id not in existing_players:
            existing_players[pl_id] = {
                "pl_id": pl_id,
                "url": f"{base}/PlayerDetails.aspx?ID={cid}&PlayerID={pl_id}",
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

    print(f"[{league['name']}] Added {added} extra roster players from {len(team_urls)} team pages.")
    return existing_players


def find_by_id_keywords(soup, keywords):
    for kw in keywords:
        el = soup.find(id=re.compile(kw, re.IGNORECASE))
        if el:
            txt = clean_text(el.get_text())
            if txt:
                return txt
    return ""


def extract_label_value_pairs(soup):
    pairs = {}
    for row in soup.find_all(["tr", "div", "li", "p"]):
        children = row.find_all(["span", "td", "th", "strong", "b", "label", "p"], recursive=False)
        if len(children) == 2:
            k = clean_text(children[0].get_text()).rstrip(":").lower()
            v = clean_text(children[1].get_text())
            if k and v and len(k) < 40 and len(v) < 80:
                pairs[k] = v
    return pairs


def enrich_player_profile(league, pl_id, data, debug_first=False):
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

        if debug_first:
            lbls = {
                el.get("id"): clean_text(el.get_text())
                for el in soup.find_all(id=re.compile(r"LBL_|Content_Main", re.IGNORECASE))
                if clean_text(el.get_text()) and len(clean_text(el.get_text())) < 80
            }
            print(f"[DEBUG] Sample PlayerDetails labels ({data['url']}): {list(lbls.items())[:20]}")

        pairs = extract_label_value_pairs(soup)

        if not name:
            name = find_by_id_keywords(soup, ["lbl_player", "playername", "lblname", "surname"])
            if not name:
                h = soup.find(["h1", "h2", "h3"])
                name = clean_text(h.get_text()) if h else f"Player {pl_id}"

        if not team:
            team = find_by_id_keywords(soup, ["lbl_team", "teamname", "lblteam"])

        raw_pos = find_by_id_keywords(soup, ["lbl_role", "position", "role", "ruolo", "post"])
        if not raw_pos:
            for k, v in pairs.items():
                if any(w in k for w in ["role", "position", "post", "rol", "ruolo"]):
                    raw_pos = v
                    break

        raw_height = find_by_id_keywords(soup, ["lbl_height", "height", "altezza", "inaltime"])
        if not raw_height:
            for k, v in pairs.items():
                if any(w in k for w in ["height", "înălțime", "inaltime", "altezza", "cm"]):
                    raw_height = v
                    break
        h_val = to_int(raw_height)
        if 140 <= h_val <= 235:
            height_cm = h_val

        raw_birth = find_by_id_keywords(soup, ["lbl_birth", "birthdate", "born", "nascita", "dob"])
        if not raw_birth:
            for k, v in pairs.items():
                if any(w in k for w in ["birth", "born", "nașter", "naster", "nascita"]):
                    raw_birth = v
                    break
        ym = re.search(r"(19\d{2}|20[0-1]\d)", raw_birth)
        if ym:
            birth_year = int(ym.group(1))
            age = datetime.now().year - birth_year

        if not nationality:
            nationality = find_by_id_keywords(soup, ["lbl_nation", "nationality", "citizenship", "country"])
            if not nationality:
                for k, v in pairs.items():
                    if any(w in k for w in ["nation", "citizen", "country", "țară", "tara"]):
                        nationality = v
                        break

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


def apply_manual_overrides(df, overrides_path="data/manual_overrides.csv"):
    """Keeps any manual heights/positions you add so daily updates never erase them."""
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
                if pd.notna(h) and int(h) > 0:
                    df.at[idx, "height_cm"] = int(h)
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

    for league in leagues:
        print(f"=== Processing {league['name']} ===")
        players = scrape_all_players_table(session, league)
        players = discover_extra_players_from_teams(session, league, players)

        items = list(players.items())
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    enrich_player_profile,
                    league,
                    pl_id,
                    pdata,
                    idx == 0,
                ): pl_id
                for idx, (pl_id, pdata) in enumerate(items)
            }
            for fut in as_completed(futures):
                rec = fut.result()
                if rec:
                    all_records.append(rec)

    os.makedirs("data", exist_ok=True)
    df = pd.DataFrame(all_records)
    if not df.empty:
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

    print(f"=== DONE: Saved {len(df)} players to data/players.csv ===")


if __name__ == "__main__":
    main()
