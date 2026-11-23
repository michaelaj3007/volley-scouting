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


def fetch_soup(session, url, timeout=25):
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"[WARN] Failed to fetch {url}: {exc}")
        return None


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


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


def discover_teams_and_players(session, league):
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", ""))

    seed_urls = [
        f"{base}/CompetitionTeamSearch.aspx?ID={cid}",
        f"{base}/CompetitionPlayerSearch.aspx?ID={cid}",
        f"{base}/CompetitionStandings.aspx?ID={cid}&PID={pid}" if pid else f"{base}/CompetitionStandings.aspx?ID={cid}",
        f"{base}/CompetitionMatches.aspx?ID={cid}&PID={pid}" if pid else f"{base}/CompetitionMatches.aspx?ID={cid}",
    ]

    team_urls = set()
    player_entries = {}  # player_id -> dict(url, name, team)

    for url in seed_urls:
        soup = fetch_soup(session, url)
        if not soup:
            continue
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = urljoin(base + "/", href)
            low = full.lower()
            if "competitionteamdetails.aspx" in low:
                team_urls.add(full)
            elif "playerdetails.aspx" in low:
                pl_id = extract_query_param(full, "PlayerID")
                if pl_id and pl_id not in player_entries:
                    player_entries[pl_id] = {
                        "url": full,
                        "name_hint": clean_text(a.get_text()),
                        "team_hint": "",
                    }

    print(f"[{league['name']}] Discovered {len(team_urls)} team pages, {len(player_entries)} initial players.")

    # Visit every team roster page to ensure 100% of players are captured
    for t_url in sorted(team_urls):
        soup = fetch_soup(session, t_url)
        if not soup:
            continue

        team_name = ""
        for sel in ["#Content_Main_LBL_TeamName", "h1", "h2", ".TeamName", "[id*='TeamName']"]:
            el = soup.select_one(sel)
            if el and clean_text(el.get_text()):
                team_name = clean_text(el.get_text())
                break

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "playerdetails.aspx" in href.lower():
                full = urljoin(base + "/", href)
                pl_id = extract_query_param(full, "PlayerID")
                if not pl_id:
                    continue
                name_txt = clean_text(a.get_text())
                if pl_id not in player_entries:
                    player_entries[pl_id] = {
                        "url": full,
                        "name_hint": name_txt,
                        "team_hint": team_name,
                    }
                else:
                    if team_name and not player_entries[pl_id]["team_hint"]:
                        player_entries[pl_id]["team_hint"] = team_name
                    if name_txt and not player_entries[pl_id]["name_hint"]:
                        player_entries[pl_id]["name_hint"] = name_txt

    print(f"[{league['name']}] Total unique players discovered: {len(player_entries)}")
    return player_entries


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
        spans = row.find_all(["span", "td", "th", "strong", "b", "label"], recursive=False)
        if len(spans) == 2:
            k = clean_text(spans[0].get_text()).rstrip(":").lower()
            v = clean_text(spans[1].get_text())
            if k and v and len(k) < 40 and len(v) < 80:
                pairs[k] = v
    return pairs


def parse_stats_from_tables(soup, debug_first=False):
    stats = {
        "matches": 0,
        "sets": 0,
        "points": 0,
        "aces": 0,
        "serve_errors": 0,
        "blocks": 0,
        "attack_points": 0,
        "attack_pct": 0.0,
        "reception_pos_pct": 0.0,
    }

    best_table = None
    best_headers = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        headers = [clean_text(c.get_text()).lower() for c in header_cells]
        joined = " ".join(headers)
        if any(k in joined for k in ["set", "pts", "tot", "ace", "blk", "block", "puncte", "bloc"]):
            best_table = table
            best_headers = headers
            break

    if not best_table:
        return stats

    if debug_first:
        print(f"[DEBUG] Sample player stats table headers: {best_headers}")

    rows = best_table.find_all("tr")[1:]
    data_rows = []
    total_row = None

    for tr in rows:
        cells = [clean_text(c.get_text()) for c in tr.find_all(["td", "th"])]
        if not cells or len(cells) < 3:
            continue
        first_cell = cells[0].lower()
        if any(w in first_cell for w in ["total", "tot.", "sum"]):
            total_row = cells
            break
        if any(ch.isdigit() for ch in " ".join(cells)):
            data_rows.append(cells)

    def col_idx(candidates):
        for cand in candidates:
            for idx, h in enumerate(best_headers):
                if cand == h or cand in h:
                    return idx
        return None

    idx_matches = col_idx(["matches", "meciuri", "match", "gp", "m"])
    idx_sets = col_idx(["sets", "seturi", "set", "s"])
    idx_points = col_idx(["points", "puncte", "pts", "tot"])
    idx_aces = col_idx(["aces", "ace", "ași", "asi", "srv ace", "as"])
    idx_blocks = col_idx(["blocks", "blocaje", "block", "blk", "bl"])
    idx_atk_pts = col_idx(["atk pts", "attack pts", "win", "exc", "atac"])
    idx_atk_pct = col_idx(["atk %", "attack %", "exc%", "spk%", "atac %"])
    idx_rec_pct = col_idx(["pos%", "rec%", "rec %", "preluare"])

    if total_row:
        target_rows = [total_row]
        is_total = True
    else:
        target_rows = data_rows
        is_total = False

    if not target_rows:
        return stats

    def get_val(row, idx):
        if idx is not None and idx < len(row):
            return row[idx]
        return "0"

    if is_total:
        r = target_rows[0]
        stats["matches"] = to_int(get_val(r, idx_matches))
        stats["sets"] = to_int(get_val(r, idx_sets))
        stats["points"] = to_int(get_val(r, idx_points))
        stats["aces"] = to_int(get_val(r, idx_aces))
        stats["blocks"] = to_int(get_val(r, idx_blocks))
        stats["attack_points"] = to_int(get_val(r, idx_atk_pts))
        stats["attack_pct"] = to_float(get_val(r, idx_atk_pct))
        stats["reception_pos_pct"] = to_float(get_val(r, idx_rec_pct))
    else:
        stats["matches"] = len(target_rows)
        stats["sets"] = sum(to_int(get_val(r, idx_sets)) for r in target_rows)
        stats["points"] = sum(to_int(get_val(r, idx_points)) for r in target_rows)
        stats["aces"] = sum(to_int(get_val(r, idx_aces)) for r in target_rows)
        stats["blocks"] = sum(to_int(get_val(r, idx_blocks)) for r in target_rows)
        stats["attack_points"] = sum(to_int(get_val(r, idx_atk_pts)) for r in target_rows)
        atk_vals = [to_float(get_val(r, idx_atk_pct)) for r in target_rows if to_float(get_val(r, idx_atk_pct)) > 0]
        rec_vals = [to_float(get_val(r, idx_rec_pct)) for r in target_rows if to_float(get_val(r, idx_rec_pct)) > 0]
        stats["attack_pct"] = round(sum(atk_vals) / len(atk_vals), 1) if atk_vals else 0.0
        stats["reception_pos_pct"] = round(sum(rec_vals) / len(rec_vals), 1) if rec_vals else 0.0

    return stats


def scrape_league_statistics_pages(session, league):
    """Also scrapes Statistics_AllPlayers.aspx / Statistics.aspx if available."""
    base = league["base_url"].rstrip("/")
    cid = str(league["competition_id"])
    pid = str(league.get("pid", ""))

    stat_urls = []
    if pid:
        stat_urls.append(f"{base}/Statistics_AllPlayers.aspx?ID={cid}&PID={pid}")
        stat_urls.append(f"{base}/Statistics.aspx?ID={cid}&PID={pid}")
    else:
        stat_urls.append(f"{base}/Statistics_AllPlayers.aspx?ID={cid}")
        stat_urls.append(f"{base}/Statistics.aspx?ID={cid}")

    league_stats = {}  # player_id -> dict

    for url in stat_urls:
        soup = fetch_soup(session, url)
        if not soup:
            continue
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [clean_text(c.get_text()).lower() for c in rows[0].find_all(["th", "td"])]
            joined = " ".join(headers)
            if not any(k in joined for k in ["player", "jucător", "jucator", "name", "nume", "pts", "tot", "ace", "blk"]):
                continue

            print(f"[DEBUG] Found stats table on {url} | headers: {headers[:15]}")

            def idx_of(keys):
                for k in keys:
                    for i, h in enumerate(headers):
                        if k == h or k in h:
                            return i
                return None

            i_sets = idx_of(["sets", "seturi", "set", "s"])
            i_pts = idx_of(["points", "puncte", "pts", "tot"])
            i_ace = idx_of(["aces", "ace", "ași", "asi", "srv"])
            i_blk = idx_of(["blocks", "blocaje", "block", "blk", "bl"])
            i_atk_p = idx_of(["atk %", "attack %", "exc%", "spk%", "atac %"])
            i_rec_p = idx_of(["pos%", "rec%", "rec %", "preluare"])

            for tr in rows[1:]:
                link = tr.find("a", href=re.compile(r"PlayerDetails\.aspx", re.IGNORECASE))
                if not link:
                    continue
                pl_id = extract_query_param(urljoin(base + "/", link["href"]), "PlayerID")
                if not pl_id:
                    continue
                cells = [clean_text(c.get_text()) for c in tr.find_all(["td", "th"])]
                entry = league_stats.setdefault(pl_id, {})

                def pull_int(idx, field):
                    if idx is not None and idx < len(cells):
                        v = to_int(cells[idx])
                        if v > entry.get(field, 0):
                            entry[field] = v

                def pull_float(idx, field):
                    if idx is not None and idx < len(cells):
                        v = to_float(cells[idx])
                        if v > entry.get(field, 0.0):
                            entry[field] = v

                pull_int(i_sets, "sets")
                pull_int(i_pts, "points")
                pull_int(i_ace, "aces")
                pull_int(i_blk, "blocks")
                pull_float(i_atk_p, "attack_pct")
                pull_float(i_rec_p, "reception_pos_pct")

    print(f"[{league['name']}] Extracted league-table stats for {len(league_stats)} players.")
    return league_stats


def parse_player_page(league, pl_id, info, league_stat_row, debug_first=False):
    session = get_session()
    soup = fetch_soup(session, info["url"])
    if not soup:
        return None

    pairs = extract_label_value_pairs(soup)

    name = (
        find_by_id_keywords(soup, ["lbl_player", "playername", "lblname", "surname"])
        or info.get("name_hint", "")
    )
    if not name:
        h = soup.find(["h1", "h2", "h3"])
        name = clean_text(h.get_text()) if h else f"Player {pl_id}"

    team = (
        find_by_id_keywords(soup, ["lbl_team", "teamname", "lblteam"])
        or info.get("team_hint", "")
    )

    raw_pos = find_by_id_keywords(soup, ["lbl_role", "position", "role", "ruolo"])
    if not raw_pos:
        for k, v in pairs.items():
            if any(w in k for w in ["role", "position", "post", "rol"]):
                raw_pos = v
                break

    raw_height = find_by_id_keywords(soup, ["lbl_height", "height", "altezza", "inaltime"])
    if not raw_height:
        for k, v in pairs.items():
            if any(w in k for w in ["height", "înălțime", "inaltime", "cm"]):
                raw_height = v
                break
    height_cm = to_int(raw_height)
    if height_cm < 140 or height_cm > 235:
        height_cm = 0

    raw_birth = find_by_id_keywords(soup, ["lbl_birth", "birthdate", "born", "nascita"])
    if not raw_birth:
        for k, v in pairs.items():
            if any(w in k for w in ["birth", "born", "nașterii", "nasterii", "data"]):
                raw_birth = v
                break
    year_match = re.search(r"(19\d{2}|20[0-1]\d)", raw_birth)
    birth_year = int(year_match.group(1)) if year_match else 0
    current_year = datetime.now().year
    age = (current_year - birth_year) if birth_year > 1950 else 0

    nationality = find_by_id_keywords(soup, ["lbl_nation", "nationality", "citizenship", "country"])
    if not nationality:
        for k, v in pairs.items():
            if any(w in k for w in ["nation", "citizen", "country", "țară", "tara", "național"]):
                nationality = v
                break

    page_stats = parse_stats_from_tables(soup, debug_first=debug_first)

    # Merge page stats with league summary stats (prefer non-zero higher values)
    merged = {}
    for field in ["matches", "sets", "points", "aces", "blocks", "attack_points", "attack_pct", "reception_pos_pct"]:
        merged[field] = max(page_stats.get(field, 0), league_stat_row.get(field, 0))

    sets = merged["sets"]
    pts_per_set = round(merged["points"] / sets, 2) if sets > 0 else 0.0
    aces_per_set = round(merged["aces"] / sets, 2) if sets > 0 else 0.0
    blocks_per_set = round(merged["blocks"] / sets, 2) if sets > 0 else 0.0

    return {
        "player_id": f"{league['country']}_{league['competition_id']}_{pl_id}",
        "league": league["name"],
        "country": league["country"],
        "gender": league.get("gender", ""),
        "name": name,
        "team": team,
        "position": normalize_position(raw_pos),
        "raw_position": raw_pos,
        "height_cm": height_cm,
        "birth_year": birth_year,
        "age": age,
        "nationality": nationality,
        "matches": merged["matches"],
        "sets": sets,
        "points": merged["points"],
        "points_per_set": pts_per_set,
        "aces": merged["aces"],
        "aces_per_set": aces_per_set,
        "blocks": merged["blocks"],
        "blocks_per_set": blocks_per_set,
        "attack_points": merged["attack_points"],
        "attack_pct": merged["attack_pct"],
        "reception_pos_pct": merged["reception_pos_pct"],
        "profile_url": info["url"],
    }


def apply_manual_overrides(df, overrides_path="data/manual_overrides.csv"):
    """Keeps any manual heights/notes you add so daily updates never erase them."""
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
                if ov_map[pid].get("height_cm", 0) and int(ov_map[pid]["height_cm"]) > 0:
                    df.at[idx, "height_cm"] = int(ov_map[pid]["height_cm"])
                if ov_map[pid].get("position"):
                    df.at[idx, "position"] = ov_map[pid]["position"]
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
        player_entries = discover_teams_and_players(session, league)
        league_stats = scrape_league_statistics_pages(session, league)

        items = list(player_entries.items())
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(
                    parse_player_page,
                    league,
                    pl_id,
                    info,
                    league_stats.get(pl_id, {}),
                    idx == 0,
                ): pl_id
                for idx, (pl_id, info) in enumerate(items)
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
