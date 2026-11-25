import json
import os
import re
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from college_scraper import (
    HEADERS,
    _enrich_college_records_from_vb,
    _find_col_idx,
    _initial_surname_key,
    _norm_hdr,
    _player_slug_from_href,
    build_european_record,
    clean,
    slugify,
    to_float,
    to_int,
)

CANADA_CACHE_PATH = "data/canada_usports_cache.json"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
    "Referer": "https://en.usports.ca/",
    "Upgrade-Insecure-Requests": "1",
}


def _load_canada_cache(season):
    if not os.path.exists(CANADA_CACHE_PATH):
        return []
    try:
        with open(CANADA_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get(season, [])
    except Exception:
        pass
    return []


def _save_canada_cache(season, records):
    if not records:
        return
    os.makedirs(os.path.dirname(CANADA_CACHE_PATH), exist_ok=True)
    data = {}
    if os.path.exists(CANADA_CACHE_PATH):
        try:
            with open(CANADA_CACHE_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    data[season] = records
    with open(CANADA_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _parse_usports_table(soup, base_url):
    best_rows = []
    for tbl in soup.find_all("table"):
        all_trs = tbl.find_all("tr")
        header_idx, headers = None, []
        for idx, tr in enumerate(all_trs[:4]):
            ths = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            if len(ths) >= 3 and any("name" in _norm_hdr(h) or "player" in _norm_hdr(h) for h in ths):
                header_idx, headers = idx, ths
                break
        if header_idx is None:
            continue

        i_name = _find_col_idx(headers, ["name", "player"])
        i_team = _find_col_idx(headers, ["team", "school"])
        i_pos = _find_col_idx(headers, ["pos", "position"])
        i_yr = _find_col_idx(headers, ["yr", "year", "cl", "class", "elig", "eligibility"])
        i_ht = _find_col_idx(headers, ["ht", "height"])
        i_gp = _find_col_idx(headers, ["gp", "mp", "m"])
        i_sp = _find_col_idx(headers, ["sp", "s", "sets"])
        i_k = _find_col_idx(headers, ["k", "kills"])
        i_e = _find_col_idx(headers, ["e", "err", "errors"])
        i_ta = _find_col_idx(headers, ["ta", "att", "total attacks"])
        i_pct = _find_col_idx(headers, ["pct", "k%", "hitting"])
        i_sa = _find_col_idx(headers, ["sa", "ace", "aces"])
        i_se = _find_col_idx(headers, ["se"])
        i_re = _find_col_idx(headers, ["re"])
        i_bs = _find_col_idx(headers, ["bs", "block solos"])
        i_ba = _find_col_idx(headers, ["ba", "block assists"])
        i_btot = _find_col_idx(headers, ["b", "blk", "tb", "tot", "total blocks"])
        i_pts = _find_col_idx(headers, ["pts", "points"])
        i_ast = _find_col_idx(headers, ["a", "ast", "assists"])
        i_dig = _find_col_idx(headers, ["d", "dig", "digs"])

        rows_out = []
        for tr in all_trs[header_idx + 1 :]:
            tds = tr.find_all(["td", "th"])
            cells = [clean(c.get_text(" ")) for c in tds]
            if len(cells) < 3 or i_name is None or i_name >= len(cells):
                continue
            name = re.sub(r"^#?\d+\s+", "", cells[i_name]).strip()
            low_n = name.lower()
            if not name or low_n in ("name", "player", "total", "totals", "team", "opponents", "opponent"):
                continue

            a_el = tds[i_name].find("a", href=True) or tr.find("a", href=re.compile(r"/(?:players|bios)/", re.IGNORECASE))
            p_url = urljoin(base_url, a_el["href"]) if a_el else base_url
            team_a = tr.find("a", href=re.compile(r"/teams/", re.IGNORECASE))

            gi = lambda idx: to_int(cells[idx]) if (idx is not None and idx < len(cells)) else 0
            gf = lambda idx: to_float(cells[idx]) if (idx is not None and idx < len(cells)) else 0.0
            gs = lambda idx: cells[idx] if (idx is not None and idx < len(cells)) else ""

            rows_out.append({
                "name": name,
                "slug": _player_slug_from_href(p_url),
                "team": gs(i_team),
                "team_url": urljoin(base_url, team_a["href"]) if team_a else "",
                "pos": gs(i_pos),
                "yr": gs(i_yr),
                "ht": gs(i_ht),
                "gp": gi(i_gp),
                "sp": gi(i_sp),
                "k": gi(i_k),
                "e": gi(i_e),
                "ta": gi(i_ta),
                "pct": gf(i_pct),
                "sa": gi(i_sa),
                "se": gi(i_se),
                "re": gi(i_re),
                "bs": gi(i_bs),
                "ba": gi(i_ba),
                "btot": gf(i_btot),
                "pts": gf(i_pts),
                "ast": gi(i_ast),
                "dig": gi(i_dig),
                "profile_url": p_url,
            })
        if len(rows_out) > len(best_rows):
            best_rows = rows_out
    return best_rows


def _parse_usports_card_roster(soup, base_url):
    out = []
    for a in soup.find_all("a", href=re.compile(r"/(?:players|bios)/", re.IGNORECASE)):
        name = re.sub(r"^#?\d+\s+", "", clean(a.get_text(" "))).strip()
        if not name or len(name.split()) < 2:
            continue
        p_url = urljoin(base_url, a["href"])
        box = a.find_parent(["li", "tr", "article", "div"])
        txt = clean(box.get_text(" | ")) if box else ""

        pos, yr, ht = "", "", ""
        m_ht = re.search(r"\b([5-7]\s*[-'′]\s*\d{1,2}|[12][.,]\d{2}\s*m?|(?:1[6-9]\d|2[0-2]\d)\s*cm)\b", txt, re.IGNORECASE)
        if m_ht:
            ht = m_ht.group(1)

        m_yr = re.search(r"(?:Yr\.?|Year|Elig\.?)\s*[:\-]?\s*([1-5](?:st|nd|rd|th)?)|\b([1-5](?:st|nd|rd|th))\b", txt, re.IGNORECASE)
        if m_yr:
            yr = m_yr.group(1) or m_yr.group(2)

        for tok in re.split(r"[|/,]", txt):
            t_clean = tok.strip().lower().replace(".", "")
            if t_clean in ("oh", "mb", "mh", "s", "opp", "rs", "l", "libero", "setter", "middle", "outside", "opposite"):
                pos = tok.strip()
                break

        out.append({
            "name": name,
            "slug": _player_slug_from_href(p_url),
            "team": "",
            "team_url": "",
            "pos": pos,
            "yr": yr,
            "ht": ht,
            "gp": 0, "sp": 0, "k": 0, "e": 0, "ta": 0, "pct": 0.0,
            "sa": 0, "se": 0, "re": 0, "bs": 0, "ba": 0, "btot": 0.0, "pts": 0.0,
            "ast": 0, "dig": 0,
            "profile_url": p_url,
        })
    return out


def _fetch_usports_soup_and_rows(session, page, url, allow_card_roster=False):
    # 1) Direct HTTP GET first (PrestoSports serves static HTML tables without headless browser blocks)
    try:
        resp = session.get(url, timeout=25)
        if resp.status_code == 200 and len(resp.text) > 1000:
            soup = BeautifulSoup(resp.text, "lxml")
            rows = _parse_usports_table(soup, url)
            if not rows and allow_card_roster:
                rows = _parse_usports_card_roster(soup, url)
            if rows:
                return soup, rows
        else:
            print(f"[DEBUG][CANADA HTTP] Status {resp.status_code} on {url}")
    except Exception as exc:
        print(f"[DEBUG][CANADA HTTP] Error on {url}: {exc}")

    # 2) Fallback to Playwright browser if needed
    if page is not None:
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=35000)
            status = resp.status if resp else 0
            if status >= 400:
                print(f"[DEBUG][CANADA PW] HTTP {status} on {url}")
                return None, []
            try:
                page.wait_for_selector("table td", state="attached", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(1200)
            html_txt = page.content()
            soup = BeautifulSoup(html_txt, "lxml")
            rows = _parse_usports_table(soup, url)
            if not rows and allow_card_roster:
                rows = _parse_usports_card_roster(soup, url)
            if not rows:
                title_txt = clean(soup.title.get_text()) if soup.title else "no-title"
                print(f"[DEBUG][CANADA PW] 0 rows on {url} (status={status}, title='{title_txt[:60]}')")
            return soup, rows
        except Exception as exc:
            print(f"[DEBUG][CANADA PW] Error on {url}: {exc}")

    return None, []


def scrape_canada_usports(browser, league):
    season = league.get("season", "2026-27")
    # Try both en.usports.ca and its underlying PrestoSports host
    main_candidates = [
        league.get("url"),
        f"https://universitysport.prestosports.com/sports/mvball/{season}/players?sort=pts&view=lineup&pos=all",
        f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&view=lineup&pos=all",
        f"https://universitysport.prestosports.com/sports/mvball/{season}/players?sort=pts&pos=all",
        f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&pos=all",
    ]
    main_candidates = [u for i, u in enumerate(main_candidates) if u and u not in main_candidates[:i]]

    session = requests.Session()
    session.headers.update(HTTP_HEADERS)

    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HTTP_HEADERS["User-Agent"])
    # Block heavy assets/trackers so pages never hang on ads
    ctx.route("**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,css}", lambda route: route.abort())
    page = ctx.new_page()

    merged, alias_to_key, team_urls = {}, {}, {}
    active_host = "https://universitysport.prestosports.com"

    def _alias_keys(name, team_name):
        keys = []
        if team_name:
            k_full = _initial_surname_key(name, team_name)
            if k_full:
                keys.append(k_full)
            first_word = team_name.split()[0]
            k_short = _initial_surname_key(name, first_word)
            if k_short and k_short not in keys:
                keys.append(k_short)
        return keys

    def upsert_player(row, default_team="", is_roster=False):
        team_name = row["team"] or default_team
        slug = row["slug"]
        akeys = _alias_keys(row["name"], team_name)

        key = None
        if slug and slug in merged:
            key = slug
        else:
            for ak in akeys:
                if ak in alias_to_key:
                    key = alias_to_key[ak]
                    break
        if not key:
            key = slug or f"{slugify(row['name'])}_{slugify(team_name)}"

        if key not in merged:
            merged[key] = {
                "name": row["name"], "team": team_name, "pos": "", "yr": "", "ht": "",
                "gp": 0, "sp": 0, "k": 0, "e": 0, "ta": 0, "pct": 0.0,
                "sa": 0, "se": 0, "re": 0, "bs": 0, "ba": 0, "btot": 0.0, "pts": 0.0,
                "ast": 0, "dig": 0,
                "profile_url": row["profile_url"],
            }
        for ak in akeys:
            alias_to_key.setdefault(ak, key)

        cur = merged[key]
        is_abbrev = bool(re.match(r"^[A-Z]\.?\s+", row["name"]))
        cur_abbrev = bool(re.match(r"^[A-Z]\.?\s+", cur["name"]))
        if (is_roster and not is_abbrev) or (cur_abbrev and not is_abbrev):
            cur["name"] = row["name"]

        for fld in ("pos", "yr", "ht"):
            if row[fld] and not cur[fld]:
                cur[fld] = row[fld]
        if team_name and not cur["team"]:
            cur["team"] = team_name
        if "/players/" in row["profile_url"]:
            cur["profile_url"] = row["profile_url"]
        for fld in ("gp", "sp", "k", "e", "ta", "sa", "se", "re", "bs", "ba", "btot", "pts", "ast", "dig"):
            cur[fld] = max(cur[fld], row[fld])
        if row["pct"] != 0.0:
            cur["pct"] = row["pct"]

    records = []
    try:
        soup_main = None
        for m_url in main_candidates:
            print(f"[{league['name']} ({season})] Fetching {m_url}")
            soup_main, rows_main = _fetch_usports_soup_and_rows(session, page, m_url)
            print(f"[DEBUG][CANADA {season}] {m_url} -> {len(rows_main)} players")
            if rows_main:
                if "en.usports.ca" in m_url:
                    active_host = "https://en.usports.ca"
                for r in rows_main:
                    upsert_player(r)
                    if r["team_url"]:
                        bt = r["team_url"].split("?")[0].rstrip("/")
                        team_urls[bt] = r["team"] or team_urls.get(bt, "")
                break

        if soup_main:
            for a in soup_main.find_all("a", href=re.compile(rf"/sports/mvball/{re.escape(season)}/teams/[a-z0-9_\-]+", re.IGNORECASE)):
                bt = urljoin(active_host + "/", a["href"]).split("?")[0].rstrip("/")
                team_urls.setdefault(bt, clean(a.get_text(" ")))

        if not team_urls:
            teams_index_url = f"{active_host}/sports/mvball/{season}/teams"
            soup_t, _ = _fetch_usports_soup_and_rows(session, page, teams_index_url)
            if soup_t:
                for a in soup_t.find_all("a", href=re.compile(rf"/sports/mvball/{re.escape(season)}/teams/[a-z0-9_\-]+", re.IGNORECASE)):
                    bt = urljoin(teams_index_url, a["href"]).split("?")[0].rstrip("/")
                    lbl = clean(a.get_text(" "))
                    if bt not in team_urls or (lbl and not team_urls[bt]):
                        team_urls[bt] = lbl

        print(f"[DEBUG][CANADA {season}] Found {len(team_urls)} teams. Reading rosters for Yr/Pos/Ht/Full Name...")
        for t_url, t_name in list(team_urls.items()):
            time.sleep(0.25)
            soup_r, rows_r = _fetch_usports_soup_and_rows(session, page, f"{t_url}?view=roster", allow_card_roster=True)
            if soup_r and not t_name and soup_r.find("h1"):
                t_name = clean(soup_r.find("h1").get_text(" "))
            for r in rows_r:
                upsert_player(r, default_team=t_name, is_roster=True)

        stat_hints = {}
        for p in merged.values():
            if p["sp"] == 0 and p["pts"] == 0 and p["k"] == 0:
                continue
            rec = build_european_record(
                league=league, name=p["name"], team=p["team"], pos_raw=p["pos"],
                class_raw=p["yr"], height_raw=p["ht"], matches=p["gp"], sets=p["sp"],
                kills=p["k"], atk_err=p["e"], atk_att=p["ta"], aces=p["sa"],
                serve_err=p["se"], bs=p["bs"], ba=p["ba"], rec_tot=0, rec_err=p["re"],
                profile_url=p["profile_url"], raw_pts=p["pts"], raw_hit_pct=p["pct"],
                raw_total_blocks=p["btot"],
            )
            stat_hints[rec["player_id"]] = {"assists": p["ast"], "digs": p["dig"]}
            records.append(rec)

        if records:
            records = _enrich_college_records_from_vb(records, stat_hints=stat_hints, max_new=200)
            _save_canada_cache(season, records)
            grad_cnt = sum(1 for r in records if r["grad_2027"])
            print(f"[{league['name']} ({season})] Scraped & cached {len(records)} Canadian players ({grad_cnt} graduating Summer 2027).")
        else:
            cached = _load_canada_cache(season)
            if cached:
                records = _enrich_college_records_from_vb(cached, max_new=200)
                print(f"[{league['name']} ({season})] Loaded {len(records)} Canadian players from local cache.")
    except Exception as exc:
        print(f"[WARN] Canada U SPORTS error ({season}): {exc}")
        cached = _load_canada_cache(season)
        if cached:
            records = cached
    finally:
        ctx.close()

    return records
