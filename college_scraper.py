import json
import os
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

ROLE_MAP = {
    "setter": "Setter", "s": "Setter",
    "opposite": "Opposite", "right side": "Opposite", "rs": "Opposite", "opp": "Opposite",
    "outside": "Outside Hitter", "oh": "Outside Hitter", "left side": "Outside Hitter", "ls": "Outside Hitter",
    "middle": "Middle Blocker", "mb": "Middle Blocker", "mh": "Middle Blocker",
    "libero": "Libero", "l": "Libero", "ds": "Libero", "l/ds": "Libero",
}

NCAA_FALLBACK_CATEGORIES = [
    ("points", "https://www.ncaa.com/stats/volleyball-men/nc/current/individual/755"),
    ("kills", "https://www.ncaa.com/stats/volleyball-men/nc/current/individual/513"),
    ("blocks", "https://www.ncaa.com/stats/volleyball-men/nc/current/individual/516"),
    ("aces", "https://www.ncaa.com/stats/volleyball-men/nc/current/individual/515"),
    ("hitting", "https://www.ncaa.com/stats/volleyball-men/nc/current/individual/512"),
    ("digs", "https://www.ncaa.com/stats/volleyball-men/nc/current/individual/517"),
    ("assists", "https://www.ncaa.com/stats/volleyball-men/nc/current/individual/514"),
]

NCAA_25_26_CACHE = "data/ncaa_2025_26_cache.json"


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def slugify(text):
    norm = unicodedata.normalize("NFKD", str(text or ""))
    ascii_txt = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", ascii_txt.lower()).strip("_") or "unknown"


def to_float(val):
    s = re.sub(r"[^0-9.\-]", "", str(val or "").strip().replace("%", "").replace(",", "."))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def to_int(val):
    return int(round(to_float(val)))


def norm_pos(raw):
    low = clean(raw).lower().replace(".", "")
    if low in ROLE_MAP:
        return ROLE_MAP[low]
    for part in re.split(r"[/,\-]", low):
        if part.strip() in ROLE_MAP:
            return ROLE_MAP[part.strip()]
    for k, v in ROLE_MAP.items():
        if len(k) > 2 and k in low:
            return v
    return clean(raw) or "Unknown"


def estimate_graduation(class_yr, season, is_canada=False):
    c = clean(class_yr).lower().replace(".", "")
    if not c:
        return "", False
    is_25_26 = "25" in str(season) and "26" in str(season)

    m_yr = re.search(r"\b([1-5])\b", c)
    if is_canada and m_yr:
        yr = int(m_yr.group(1))
        if is_25_26:
            if yr in (3, 4):
                return "🎓 Καλοκαίρι 2027", True
            if yr == 5:
                return "2026 (Αποφοίτησε)", False
            return f"202{8 + (2 - yr)}+", False
        if yr in (4, 5):
            return "🎓 Καλοκαίρι 2027", True
        return f"202{7 + (4 - yr)}", False

    if is_25_26:
        if any(k in c for k in ["jr", "junior", "r-so", "rs so", "redshirt sophomore"]):
            return "🎓 Καλοκαίρι 2027", True
        if any(k in c for k in ["sr", "senior", "gr", "grad", "5th"]):
            return "2026 (Αποφοίτησε)", False
        if any(k in c for k in ["so", "soph"]):
            return "2028", False
        if any(k in c for k in ["fr", "fresh"]):
            return "2029", False
    else:
        if any(k in c for k in ["sr", "senior", "gr", "grad", "5th", "r-jr", "rs jr"]):
            return "🎓 Καλοκαίρι 2027", True
        if any(k in c for k in ["jr", "junior"]):
            return "2028", False
        if any(k in c for k in ["so", "soph"]):
            return "2029", False
        if any(k in c for k in ["fr", "fresh"]):
            return "2030", False
    return clean(class_yr), False


def parse_height_to_cm(raw_h):
    s = clean(raw_h)
    if not s:
        return 0
    m_ft = re.search(r"([5-7])\s*(?:ft\.?|feet|'|′|-)\s*(\d{1,2})", s, re.IGNORECASE)
    if m_ft and 0 <= int(m_ft.group(2)) <= 11:
        cm = int(round((int(m_ft.group(1)) * 12 + int(m_ft.group(2))) * 2.54))
        return cm if 150 <= cm <= 235 else 0
    m_m = re.search(r"\b([12])[.,](\d{2})\s*m?\b", s, re.IGNORECASE)
    if m_m:
        cm = int(m_m.group(1)) * 100 + int(m_m.group(2))
        return cm if 150 <= cm <= 235 else 0
    cm = to_int(s)
    return cm if 150 <= cm <= 235 else 0


def _norm_hdr(text):
    s = clean(text).lower().replace(".", "")
    s = re.sub(r"[^a-z0-9/%#\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _hdr_tokens(h):
    nh = _norm_hdr(h)
    parts = nh.split()
    return nh, (parts[0] if parts else "")


def _find_col_idx(headers, candidates):
    parsed = [_hdr_tokens(h) for h in headers]
    for cand in candidates:
        c = _norm_hdr(cand)
        for i, (full_h, first_tok) in enumerate(parsed):
            if full_h == c or first_tok == c:
                return i
    for cand in candidates:
        c = _norm_hdr(cand)
        if len(c) < 4:
            continue
        for i, (full_h, _) in enumerate(parsed):
            if c in full_h:
                return i
    return None


def build_european_record(
    league, name, team, pos_raw, class_raw, height_raw,
    matches, sets, kills, atk_err, atk_att, aces, serve_err,
    bs, ba, rec_tot, rec_err, profile_url,
    raw_pts=0.0, raw_hit_pct=0.0, raw_total_blocks=0.0
):
    season = league.get("season", "2026-27")
    is_can = league["country"] == "Canada"

    # European-equivalent blocks: Solo (1.0) + half of shared Block Assists (0.5 each)
    if bs > 0 or ba > 0:
        eu_blocks = int(round(bs + 0.5 * ba))
    elif raw_total_blocks > 0:
        eu_blocks = int(round(raw_total_blocks))
    else:
        eu_blocks = 0

    eu_points = int(kills + aces + eu_blocks)
    if eu_points == 0 and raw_pts > 0:
        eu_points = int(round(raw_pts))

    atk_pct = round(100.0 * kills / atk_att, 1) if atk_att > 0 else 0.0
    if atk_att > 0:
        atk_eff_pct = round(100.0 * (kills - atk_err) / atk_att, 1)
    elif raw_hit_pct != 0.0:
        atk_eff_pct = round(raw_hit_pct * 100.0, 1) if abs(raw_hit_pct) <= 1.0 else round(raw_hit_pct, 1)
    else:
        atk_eff_pct = 0.0

    rec_pos_pct = round(100.0 * max(0, rec_tot - rec_err) / rec_tot, 1) if rec_tot > 0 else 0.0
    grad_label, is_grad_2027 = estimate_graduation(class_raw, season, is_canada=is_can)
    pid = f"{league['country']}_{league['competition_id']}_{slugify(season)}_{slugify(name)}_{slugify(team)}"

    return {
        "player_id": pid, "season": season, "league": league["name"],
        "country": league["country"], "gender": league.get("gender", "M"),
        "name": name, "team": team, "position": norm_pos(pos_raw), "raw_position": pos_raw,
        "class_year": clean(class_raw), "grad_status": grad_label, "grad_2027": is_grad_2027,
        "height_cm": parse_height_to_cm(height_raw), "birth_year": 0, "age": 0, "nationality": "",
        "matches": int(matches), "sets": int(sets),
        "points": eu_points, "points_per_set": round(eu_points / sets, 2) if sets > 0 else 0.0,
        "break_points": 0, "won_lost": 0, "serve_total": 0,
        "aces": int(aces), "aces_per_set": round(aces / sets, 2) if sets > 0 else 0.0,
        "serve_errors": int(serve_err),
        "blocks": eu_blocks, "blocks_per_set": round(eu_blocks / sets, 2) if sets > 0 else 0.0,
        "attack_total": int(atk_att), "attack_points": int(kills),
        "attack_errors": int(atk_err), "attack_blocked": 0,
        "attack_pct": atk_pct, "attack_eff_pct": atk_eff_pct,
        "reception_total": int(rec_tot), "reception_pos_pct": rec_pos_pct, "reception_perf_pct": 0.0,
        "profile_url": profile_url, "photo_url": "",
    }


def _fetch_html_pw(ctx, page, url, timeout_ms=30000):
    try:
        resp = ctx.request.get(url, timeout=timeout_ms)
        if resp.status == 200 and "<table" in resp.text().lower():
            return resp.text()
    except Exception:
        pass
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if resp and resp.status >= 400:
            return ""
        page.wait_for_selector("table", state="attached", timeout=12000)
        page.wait_for_timeout(800)
        return page.content()
    except Exception:
        return ""


def _player_slug_from_href(href):
    if not href:
        return ""
    m = re.search(r"/players/([^/?#]+)", urlparse(href).path.rstrip("/"), re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _initial_surname_key(name, team):
    norm = unicodedata.normalize("NFKD", str(name or ""))
    ascii_n = "".join(ch for ch in norm if not unicodedata.combining(ch)).lower()
    if "," in ascii_n:
        parts = [p.strip() for p in ascii_n.split(",", 1)]
        last_n, first_n = parts[0], parts[1]
    else:
        toks = [t for t in re.sub(r"[^a-z\-\s]", " ", ascii_n).split() if t]
        if len(toks) < 2:
            return ""
        first_n, last_n = toks[0], toks[-1]
    if not first_n or not last_n:
        return ""
    return f"{slugify(team)}_{first_n[0]}_{slugify(last_n)}"


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
                "name": name, "slug": _player_slug_from_href(p_url),
                "team": gs(i_team), "team_url": urljoin(base_url, team_a["href"]) if team_a else "",
                "pos": gs(i_pos), "yr": gs(i_yr), "ht": gs(i_ht),
                "gp": gi(i_gp), "sp": gi(i_sp), "k": gi(i_k), "e": gi(i_e), "ta": gi(i_ta),
                "pct": gf(i_pct), "sa": gi(i_sa), "se": gi(i_se), "re": gi(i_re),
                "bs": gi(i_bs), "ba": gi(i_ba), "btot": gf(i_btot), "pts": gf(i_pts),
                "profile_url": p_url,
            })
        if len(rows_out) > len(best_rows):
            best_rows = rows_out
    return best_rows


def _fetch_usports_page_rows(ctx, page, url, timeout_ms=30000):
    # First try lightweight fetch; fall back to full browser render if JS populates the table
    soup = None
    rows = []
    try:
        resp = ctx.request.get(url, timeout=timeout_ms)
        if resp.status == 200:
            soup = BeautifulSoup(resp.text(), "lxml")
            rows = _parse_usports_table(soup, url)
            if rows:
                return soup, rows
    except Exception:
        pass
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if resp and resp.status >= 400:
            return soup, []
        page.wait_for_selector("table", state="attached", timeout=15000)
        page.wait_for_timeout(1200)
        soup = BeautifulSoup(page.content(), "lxml")
        rows = _parse_usports_table(soup, url)
    except Exception:
        pass
    return soup, rows


def scrape_usports_canada(browser, league):
    season = league.get("season", "2026-27")
    # U SPORTS splits stats across 3 PrestoSports views: Offense (of), Defense/Blocks (df), Serve/Receive (sr)
    stat_urls = [
        ("offense", f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&pos=of&r=0"),
        ("offense_q", f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&view=lineup&pos=of"),
        ("defense", f"https://en.usports.ca/sports/mvball/{season}/players?sort=b&pos=df&r=0"),
        ("serve_rec", f"https://en.usports.ca/sports/mvball/{season}/players?sort=sa&pos=sr&r=0"),
    ]
    teams_index_url = f"https://en.usports.ca/sports/mvball/{season}/teams"

    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    merged = {}
    alias_to_key = {}
    team_urls = {}

    def upsert_player(row, default_team="", is_roster=False):
        team_name = row["team"] or default_team
        slug = row["slug"]
        isk = _initial_surname_key(row["name"], team_name)

        key = None
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
                "profile_url": row["profile_url"],
            }
        if isk:
            alias_to_key.setdefault(isk, key)

        cur = merged[key]
        # Prefer full name ("Kaden Schmidt") over abbreviated ("K Schmidt")
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
        for fld in ("gp", "sp", "k", "e", "ta", "sa", "se", "re", "bs", "ba", "btot", "pts"):
            cur[fld] = max(cur[fld], row[fld])
        if row["pct"] != 0.0:
            cur["pct"] = row["pct"]

    try:
        # 1) Pull all 3 league-wide stats tabs (Offense, Defense/Blocks, Serve/Receive)
        for label, s_url in stat_urls:
            soup_s, rows_s = _fetch_usports_page_rows(ctx, page, s_url, timeout_ms=35000)
            print(f"[DEBUG][USPORTS {season}] {label}: {len(rows_s)} rows")
            for r in rows_s:
                upsert_player(r)
                if r["team_url"]:
                    bt = r["team_url"].split("?")[0].rstrip("/")
                    team_urls[bt] = r["team"] or team_urls.get(bt, "")
            if soup_s:
                for a in soup_s.find_all("a", href=re.compile(rf"/sports/mvball/{re.escape(season)}/teams/[a-z0-9_\-]+", re.IGNORECASE)):
                    bt = urljoin(s_url, a["href"]).split("?")[0].rstrip("/")
                    team_urls.setdefault(bt, clean(a.get_text(" ")))

        # 2) Discover any remaining schools from the teams index
        soup_t, _ = _fetch_usports_page_rows(ctx, page, teams_index_url, timeout_ms=25000)
        if soup_t:
            for a in soup_t.find_all("a", href=re.compile(rf"/sports/mvball/{re.escape(season)}/teams/[a-z0-9_\-]+", re.IGNORECASE)):
                bt = urljoin(teams_index_url, a["href"]).split("?")[0].rstrip("/")
                lbl = clean(a.get_text(" "))
                if bt not in team_urls or (lbl and not team_urls[bt]):
                    team_urls[bt] = lbl

        # 3) Pull each team's roster (?view=roster) for Full Name, Position, Year (Yr -> Grad 2027), Height
        print(f"[DEBUG][USPORTS {season}] Enriching bios from {len(team_urls)} team rosters...")
        roster_hits = 0
        for t_url, t_name in list(team_urls.items()):
            soup_r, rows_r = _fetch_usports_page_rows(ctx, page, f"{t_url}?view=roster", timeout_ms=20000)
            if soup_r and not t_name and soup_r.find("h1"):
                t_name = clean(soup_r.find("h1").get_text(" "))
            for r in rows_r:
                upsert_player(r, default_team=t_name, is_roster=True)
                roster_hits += 1
        print(f"[DEBUG][USPORTS {season}] Matched {roster_hits} roster entries.")

        records = [
            build_european_record(
                league=league, name=p["name"], team=p["team"], pos_raw=p["pos"],
                class_raw=p["yr"], height_raw=p["ht"], matches=p["gp"], sets=p["sp"],
                kills=p["k"], atk_err=p["e"], atk_att=p["ta"], aces=p["sa"],
                serve_err=p["se"], bs=p["bs"], ba=p["ba"], rec_tot=0, rec_err=p["re"],
                profile_url=p["profile_url"], raw_pts=p["pts"], raw_hit_pct=p["pct"],
                raw_total_blocks=p["btot"],
            )
            for p in merged.values()
            if p["sp"] > 0 or p["pts"] > 0 or p["k"] > 0
        ]
        grad_cnt = sum(1 for r in records if r["grad_2027"])
        print(f"[{league['name']} ({season})] Scraped {len(records)} players (EU-converted, {grad_cnt} graduating Summer 2027).")
        return records
    except Exception as exc:
        print(f"[WARN] U SPORTS error ({season}): {exc}")
        return []
    finally:
        ctx.close()


def _discover_ncaa_categories(soup, base_url):
    wanted = {
        "points": ["points per set", "points"],
        "kills": ["kills per set", "kills"],
        "blocks": ["blocks per set", "blocks"],
        "aces": ["aces per set", "service aces", "aces"],
        "hitting": ["hitting percentage", "hitting"],
        "digs": ["digs per set", "digs"],
        "assists": ["assists per set", "assists"],
    }
    found = {}
    for el in soup.find_all(["option", "a"]):
        ref = clean(el.get("value") or el.get("href"))
        txt = clean(el.get_text(" ")).lower()
        if "/individual/" not in ref:
            continue
        full_u = urljoin(base_url, ref)
        for key, phrases in wanted.items():
            if key not in found and any(p in txt for p in phrases):
                found[key] = full_u

    out = list(found.items())
    seen = {u for _, u in out}
    for k, u in NCAA_FALLBACK_CATEGORIES:
        if k not in found and u not in seen:
            out.append((k, u))
    return out


def scrape_ncaa_mens(browser, league):
    season = league.get("season", "2026-27")
    index_url = league.get("url") or "https://www.ncaa.com/stats/volleyball-men/nc"

    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        print(f"[{league['name']} ({season})] Opening {index_url}")
        html_idx = _fetch_html_pw(ctx, page, index_url, timeout_ms=40000)
        soup_idx = BeautifulSoup(html_idx, "lxml") if html_idx else None
        page_txt = clean(soup_idx.get_text(" ")) if soup_idx else ""
        active_is_26_27 = bool(re.search(r"\b2026\s*[-–]\s*27\b", page_txt))

        if season == "2026-27" and not active_is_26_27:
            print(f"[{league['name']} ({season})] NCAA Men's 2026-27 starts in January 2027 (not active yet).")
            return []
        if season == "2025-26" and active_is_26_27:
            if os.path.exists(NCAA_25_26_CACHE):
                with open(NCAA_25_26_CACHE, "r", encoding="utf-8") as f:
                    cached = json.load(f)
                print(f"[{league['name']} ({season})] Loaded {len(cached)} players from saved 2025-26 archive.")
                return cached
            return []

        categories = _discover_ncaa_categories(soup_idx, index_url) if soup_idx else NCAA_FALLBACK_CATEGORIES
        merged = {}
        for cat, base_cat_url in categories:
            for page_no in range(1, 6):
                url = base_cat_url if page_no == 1 else f"{base_cat_url.rstrip('/')}/p{page_no}"
                html_cat = _fetch_html_pw(ctx, page, url, timeout_ms=30000)
                if not html_cat:
                    break
                tbl = BeautifulSoup(html_cat, "lxml").find("table")
                if not tbl:
                    break

                headers = [clean(th.get_text(" ")) for th in tbl.find_all("th")]
                i_name = _find_col_idx(headers, ["name", "player"])
                i_team = _find_col_idx(headers, ["team"])
                i_cl = _find_col_idx(headers, ["cl", "class", "yr"])
                i_pos = _find_col_idx(headers, ["pos", "position"])
                i_ht = _find_col_idx(headers, ["ht", "height"])
                i_s = _find_col_idx(headers, ["s", "sp", "sets"])
                i_k = _find_col_idx(headers, ["kills", "k"])
                i_e = _find_col_idx(headers, ["errors", "e"])
                i_ta = _find_col_idx(headers, ["total attacks", "ta", "att"])
                i_pct = _find_col_idx(headers, ["pct", "hitting"])
                i_sa = _find_col_idx(headers, ["aces", "sa"])
                i_bs = _find_col_idx(headers, ["block solos", "bs"])
                i_ba = _find_col_idx(headers, ["block assists", "ba"])
                i_btot = _find_col_idx(headers, ["total blocks", "tot", "blk"])
                i_pts = _find_col_idx(headers, ["points", "pts"])

                rows_added = 0
                for tr in tbl.find_all("tr"):
                    cells = [clean(td.get_text(" ")) for td in tr.find_all("td")]
                    if len(cells) < 5 or i_name is None or i_name >= len(cells):
                        continue
                    name = cells[i_name]
                    if not name or name.lower() in ("name", "player", "total", "-"):
                        continue
                    team = cells[i_team] if (i_team is not None and i_team < len(cells)) else ""
                    key = f"{slugify(name)}_{slugify(team)}"
                    a_el = tr.find("a", href=True)
                    p_url = urljoin(url, a_el["href"]) if a_el else url

                    gi = lambda idx: to_int(cells[idx]) if (idx is not None and idx < len(cells)) else 0
                    gf = lambda idx: to_float(cells[idx]) if (idx is not None and idx < len(cells)) else 0.0

                    entry = merged.setdefault(key, {
                        "name": name, "team": team, "pos": "", "cl": "", "ht": "",
                        "sets": 0, "kills": 0, "err": 0, "att": 0, "hit_pct": 0.0,
                        "aces": 0, "bs": 0, "ba": 0, "btot": 0.0, "raw_pts": 0.0, "url": p_url,
                    })
                    for fld, idx in (("pos", i_pos), ("cl", i_cl), ("ht", i_ht)):
                        if idx is not None and idx < len(cells) and cells[idx] not in ("", "-"):
                            entry[fld] = cells[idx]
                    entry["sets"] = max(entry["sets"], gi(i_s))
                    for fld, idx in (("kills", i_k), ("err", i_e), ("att", i_ta), ("aces", i_sa), ("bs", i_bs), ("ba", i_ba)):
                        if idx is not None:
                            entry[fld] = max(entry[fld], gi(idx))
                    if i_pct is not None and gf(i_pct) != 0.0:
                        entry["hit_pct"] = gf(i_pct)
                    if i_btot is not None:
                        entry["btot"] = max(entry["btot"], gf(i_btot))
                    if i_pts is not None:
                        entry["raw_pts"] = max(entry["raw_pts"], gf(i_pts))
                    rows_added += 1
                if rows_added == 0:
                    break
            print(f"[DEBUG][NCAA {season}] {cat}: {len(merged)} unique players so far")

        records = [
            build_european_record(
                league=league, name=e["name"], team=e["team"], pos_raw=e["pos"],
                class_raw=e["cl"], height_raw=e["ht"], matches=0, sets=e["sets"],
                kills=e["kills"], atk_err=e["err"], atk_att=e["att"], aces=e["aces"],
                serve_err=0, bs=e["bs"], ba=e["ba"], rec_tot=0, rec_err=0,
                profile_url=e["url"], raw_pts=e["raw_pts"], raw_hit_pct=e["hit_pct"],
                raw_total_blocks=e["btot"],
            )
            for e in merged.values()
        ]
        if season == "2025-26" and records:
            os.makedirs("data", exist_ok=True)
            with open(NCAA_25_26_CACHE, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)

        grad_cnt = sum(1 for r in records if r["grad_2027"])
        print(f"[{league['name']} ({season})] Scraped {len(records)} players (EU-converted, {grad_cnt} graduating Summer 2027).")
        return records
    except Exception as exc:
        print(f"[WARN] NCAA error ({season}): {exc}")
        return []
    finally:
        ctx.close()


def scrape_college_league(browser, league):
    ltype = league.get("type", "")
    if ltype == "usports_ca":
        return scrape_usports_canada(browser, league)
    if ltype == "ncaa_us":
        return scrape_ncaa_mens(browser, league)
    return []
