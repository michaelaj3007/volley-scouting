import re
import time
from urllib.parse import urljoin

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


def _parse_usports_table(soup, base_url):
    best_rows = []
    for tbl in soup.find_all("table"):
        header_row, headers = None, []
        for tr in tbl.find_all("tr")[:4]:
            ths = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            if len(ths) >= 3 and any("name" in _norm_hdr(h) or "player" in _norm_hdr(h) for h in ths):
                header_row, headers = tr, ths
                break
        if not header_row:
            continue

        i_name = _find_col_idx(headers, ["name", "player"])
        i_team = _find_col_idx(headers, ["team", "school"])
        i_pos = _find_col_idx(headers, ["pos", "position"])
        i_yr = _find_col_idx(headers, ["yr", "year", "cl", "class", "elig"])
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
        for tr in tbl.find_all("tr"):
            if tr is header_row:
                continue
            tds = tr.find_all(["td", "th"])
            cells = [clean(c.get_text(" ")) for c in tds]
            if len(cells) < 3 or i_name is None or i_name >= len(cells):
                continue
            name = cells[i_name]
            low_n = name.lower()
            if not name or low_n in ("name", "player", "total", "totals", "team", "opponents", "opponent"):
                continue

            a_el = tds[i_name].find("a", href=True) or tr.find("a", href=re.compile(r"/players/", re.IGNORECASE))
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


def _load_page_rows(ctx, page, url, timeout_ms=35000):
    soup, rows = None, []
    try:
        resp = ctx.request.get(url, timeout=timeout_ms)
        if resp.status == 200 and "<table" in resp.text().lower():
            soup = BeautifulSoup(resp.text(), "lxml")
            rows = _parse_usports_table(soup, url)
            if rows:
                return soup, rows
    except Exception:
        pass

    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if resp and resp.status >= 400:
            print(f"[DEBUG][CANADA] HTTP {resp.status} on {url}")
            return soup, []
        page.wait_for_selector("table", state="attached", timeout=18000)
        page.wait_for_timeout(1500)
        soup = BeautifulSoup(page.content(), "lxml")
        rows = _parse_usports_table(soup, url)
    except Exception as exc:
        print(f"[DEBUG][CANADA] Note on {url}: {exc}")
    return soup, rows


def scrape_canada_usports(browser, league):
    season = league.get("season", "2026-27")
    main_candidates = [
        league.get("url"),
        f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&view=lineup&pos=all",
        f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&pos=all",
        f"https://universitysport.prestosports.com/sports/mvball/{season}/players?sort=pts&view=lineup&pos=all",
    ]
    main_candidates = [u for i, u in enumerate(main_candidates) if u and u not in main_candidates[:i]]
    teams_index_url = f"https://en.usports.ca/sports/mvball/{season}/teams"

    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    merged, alias_to_key, team_urls = {}, {}, {}

    def upsert_player(row, default_team="", is_roster=False):
        team_name = row["team"] or default_team
        slug = row["slug"]
        isk = _initial_surname_key(row["name"], team_name)

        if slug and slug in merged:
            key = slug
        elif isk and isk in alias_to_key:
            key = alias_to_key[isk]
        else:
            key = slug or f"{slugify(row['name'])}_{slugify(team_name)}"

        if key not in merged:
            merged[key] = {
                "name": row["name"], "team": team_name, "pos": "", "yr": "", "ht": "",
                "gp": 0, "sp": 0, "k": 0, "e": 0, "ta": 0, "pct": 0.0,
                "sa": 0, "se": 0, "re": 0, "bs": 0, "ba": 0, "btot": 0.0, "pts": 0.0,
                "ast": 0, "dig": 0,
                "profile_url": row["profile_url"],
            }
        if isk:
            alias_to_key.setdefault(isk, key)

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
            print(f"[{league['name']} ({season})] Opening {m_url}")
            soup_main, rows_main = _load_page_rows(ctx, page, m_url, timeout_ms=40000)
            print(f"[DEBUG][CANADA {season}] {m_url} -> {len(rows_main)} rows")
            if rows_main:
                for r in rows_main:
                    upsert_player(r)
                    if r["team_url"]:
                        bt = r["team_url"].split("?")[0].rstrip("/")
                        team_urls[bt] = r["team"] or team_urls.get(bt, "")
                break

        if soup_main:
            for a in soup_main.find_all("a", href=re.compile(rf"/sports/mvball/{re.escape(season)}/teams/[a-z0-9_\-]+", re.IGNORECASE)):
                bt = urljoin("https://en.usports.ca/", a["href"]).split("?")[0].rstrip("/")
                team_urls.setdefault(bt, clean(a.get_text(" ")))

        soup_t, _ = _load_page_rows(ctx, page, teams_index_url, timeout_ms=25000)
        if soup_t:
            for a in soup_t.find_all("a", href=re.compile(rf"/sports/mvball/{re.escape(season)}/teams/[a-z0-9_\-]+", re.IGNORECASE)):
                bt = urljoin(teams_index_url, a["href"]).split("?")[0].rstrip("/")
                lbl = clean(a.get_text(" "))
                if bt not in team_urls or (lbl and not team_urls[bt]):
                    team_urls[bt] = lbl

        print(f"[DEBUG][CANADA {season}] Found {len(team_urls)} teams. Reading rosters & stats...")
        for t_url, t_name in list(team_urls.items()):
            time.sleep(0.2)
            soup_r, rows_r = _load_page_rows(ctx, page, f"{t_url}?view=roster", timeout_ms=20000)
            if soup_r and not t_name and soup_r.find("h1"):
                t_name = clean(soup_r.find("h1").get_text(" "))
            for r in rows_r:
                upsert_player(r, default_team=t_name, is_roster=True)

            time.sleep(0.2)
            _, rows_ts = _load_page_rows(ctx, page, f"{t_url}?view=lineup", timeout_ms=20000)
            for r in rows_ts:
                upsert_player(r, default_team=t_name, is_roster=False)

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

        records = _enrich_college_records_from_vb(records, stat_hints=stat_hints, max_new=150)
        grad_cnt = sum(1 for r in records if r["grad_2027"])
        print(f"[{league['name']} ({season})] Scraped {len(records)} Canadian players ({grad_cnt} graduating Summer 2027).")
    except Exception as exc:
        print(f"[WARN] Canada U SPORTS error ({season}): {exc}")
    finally:
        ctx.close()

    return records
