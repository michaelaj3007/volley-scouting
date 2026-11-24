import re
import unicodedata
from urllib.parse import urljoin

from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

ROLE_MAP = {
    "setter": "Setter",
    "opposite": "Opposite",
    "right side": "Opposite",
    "rs": "Opposite",
    "opp": "Opposite",
    "outside": "Outside Hitter",
    "oh": "Outside Hitter",
    "left side": "Outside Hitter",
    "ls": "Outside Hitter",
    "middle": "Middle Blocker",
    "mb": "Middle Blocker",
    "libero": "Libero",
    "l": "Libero",
    "ds": "Libero",
}


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
    low = clean(raw).lower()
    if low in ROLE_MAP:
        return ROLE_MAP[low]
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
        else:
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
    m_ft = re.search(r"(\d)\s*[-'′]\s*(\d{1,2})", s)
    if m_ft:
        ft, inch = int(m_ft.group(1)), int(m_ft.group(2))
        cm = int(round((ft * 12 + inch) * 2.54))
        return cm if 150 <= cm <= 235 else 0
    cm = to_int(s)
    return cm if 150 <= cm <= 235 else 0


def build_european_record(
    league, name, team, pos_raw, class_raw, height_raw,
    matches, sets, kills, atk_err, atk_att, aces, serve_err,
    bs, ba, rec_tot, rec_err, profile_url, nationality
):
    season = league.get("season", "2026-27")
    is_can = league["country"] == "Canada"

    # European-equivalent blocks: Solo (1.0) + half of shared Block Assists (0.5 each)
    eu_blocks = int(round(bs + 0.5 * ba))
    eu_points = int(kills + aces + eu_blocks)

    atk_pct = round(100.0 * kills / atk_att, 1) if atk_att > 0 else 0.0
    atk_eff_pct = round(100.0 * (kills - atk_err) / atk_att, 1) if atk_att > 0 else 0.0
    rec_pos_pct = round(100.0 * max(0, rec_tot - rec_err) / rec_tot, 1) if rec_tot > 0 else 0.0

    grad_label, is_grad_2027 = estimate_graduation(class_raw, season, is_canada=is_can)
    pid = f"{league['country']}_{league['competition_id']}_{slugify(season)}_{slugify(name)}_{slugify(team)}"

    return {
        "player_id": pid,
        "season": season,
        "league": league["name"],
        "country": league["country"],
        "gender": league.get("gender", "M"),
        "name": name,
        "team": team,
        "position": norm_pos(pos_raw),
        "raw_position": pos_raw,
        "class_year": clean(class_raw),
        "grad_status": grad_label,
        "grad_2027": is_grad_2027,
        "height_cm": parse_height_to_cm(height_raw),
        "birth_year": 0,
        "age": 0,
        "nationality": nationality,
        "matches": int(matches),
        "sets": int(sets),
        "points": eu_points,
        "points_per_set": round(eu_points / sets, 2) if sets > 0 else 0.0,
        "break_points": 0,
        "won_lost": 0,
        "serve_total": 0,
        "aces": int(aces),
        "aces_per_set": round(aces / sets, 2) if sets > 0 else 0.0,
        "serve_errors": int(serve_err),
        "blocks": eu_blocks,
        "blocks_per_set": round(eu_blocks / sets, 2) if sets > 0 else 0.0,
        "attack_total": int(atk_att),
        "attack_points": int(kills),
        "attack_errors": int(atk_err),
        "attack_blocked": 0,
        "attack_pct": atk_pct,
        "attack_eff_pct": atk_eff_pct,
        "reception_total": int(rec_tot),
        "reception_pos_pct": rec_pos_pct,
        "reception_perf_pct": 0.0,
        "profile_url": profile_url,
        "photo_url": "",
    }


def _find_col_idx(headers, candidates):
    for cand in candidates:
        for i, h in enumerate(headers):
            if h == cand or cand in h:
                return i
    return None


def scrape_usports_canada(browser, league):
    season = league.get("season", "2026-27")
    url = league.get("url") or f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&view=lineup&pos=all"
    records = []
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        print(f"[{league['name']} ({season})] Opening {url}")
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_selector("table", timeout=15000)
        soup = BeautifulSoup(page.content(), "lxml")

        target_table = None
        headers = []
        for tbl in soup.find_all("table"):
            ths = [clean(th.get_text(" ")).lower() for th in tbl.find_all("th")]
            if any("name" in h or "player" in h for h in ths) and any(h in ("k", "kills", "sp", "s") for h in ths):
                target_table = tbl
                headers = ths
                break

        if not target_table:
            print(f"[WARN] Could not find stats table on {url}")
            return []

        i_name = _find_col_idx(headers, ["name", "player"])
        i_team = _find_col_idx(headers, ["team", "school"])
        i_pos = _find_col_idx(headers, ["pos"])
        i_yr = _find_col_idx(headers, ["yr", "cl", "year", "class"])
        i_ht = _find_col_idx(headers, ["ht", "height"])
        i_mp = _find_col_idx(headers, ["gp", "mp", "m"])
        i_sp = _find_col_idx(headers, ["sp", "s", "sets"])
        i_k = _find_col_idx(headers, ["k", "kills"])
        i_e = _find_col_idx(headers, ["e", "err"])
        i_ta = _find_col_idx(headers, ["ta", "att"])
        i_sa = _find_col_idx(headers, ["sa", "ace"])
        i_se = _find_col_idx(headers, ["se"])
        i_bs = _find_col_idx(headers, ["bs"])
        i_ba = _find_col_idx(headers, ["ba"])
        i_re = _find_col_idx(headers, ["re"])

        for tr in target_table.find_all("tr"):
            cells = [clean(td.get_text(" ")) for td in tr.find_all("td")]
            if len(cells) < 8 or i_name is None or i_name >= len(cells):
                continue
            name = cells[i_name]
            if not name or name.lower() in ("total", "totals", "team"):
                continue

            a_el = tr.find("a", href=True)
            p_url = urljoin(url, a_el["href"]) if a_el else url
            team = cells[i_team] if (i_team is not None and i_team < len(cells)) else ""
            pos = cells[i_pos] if (i_pos is not None and i_pos < len(cells)) else ""
            yr = cells[i_yr] if (i_yr is not None and i_yr < len(cells)) else ""
            ht = cells[i_ht] if (i_ht is not None and i_ht < len(cells)) else ""

            def num(idx):
                return to_int(cells[idx]) if (idx is not None and idx < len(cells)) else 0

            records.append(
                build_european_record(
                    league=league, name=name, team=team, pos_raw=pos, class_raw=yr, height_raw=ht,
                    matches=num(i_mp), sets=num(i_sp), kills=num(i_k), atk_err=num(i_e), atk_att=num(i_ta),
                    aces=num(i_sa), serve_err=num(i_se), bs=num(i_bs), ba=num(i_ba),
                    rec_tot=0, rec_err=num(i_re), profile_url=p_url, nationality="CAN",
                )
            )
        print(f"[{league['name']} ({season})] Scraped {len(records)} players (EU-converted).")
    except Exception as exc:
        print(f"[WARN] U SPORTS error ({season}): {exc}")
    finally:
        ctx.close()
    return records


def scrape_ncaa_mens(browser, league):
    season = league.get("season", "2026-27")
    url = league.get("url") or "https://stats.ncaa.org/rankings/national_ranking?academic_year=2026&division=1&sport_code=MVB"
    records = []
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        print(f"[{league['name']} ({season})] Opening {url}")
        page.goto(url, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(2500)
        soup = BeautifulSoup(page.content(), "lxml")

        for tbl in soup.find_all("table"):
            ths = [clean(th.get_text(" ")).lower() for th in tbl.find_all("th")]
            if not any("player" in h or "name" in h for h in ths):
                continue
            i_name = _find_col_idx(ths, ["player", "name"])
            i_team = _find_col_idx(ths, ["team"])
            i_cl = _find_col_idx(ths, ["cl", "class", "yr"])
            i_pos = _find_col_idx(ths, ["pos"])
            i_ht = _find_col_idx(ths, ["ht"])
            i_s = _find_col_idx(ths, ["s", "sets"])
            i_k = _find_col_idx(ths, ["kills", "k"])
            i_e = _find_col_idx(ths, ["errors", "e"])
            i_ta = _find_col_idx(ths, ["total attacks", "ta", "att"])
            i_sa = _find_col_idx(ths, ["aces", "sa"])
            i_bs = _find_col_idx(ths, ["block solos", "bs"])
            i_ba = _find_col_idx(ths, ["block assists", "ba"])

            for tr in tbl.find_all("tr"):
                cells = [clean(td.get_text(" ")) for td in tr.find_all("td")]
                if len(cells) < 6 or i_name is None or i_name >= len(cells):
                    continue
                name = cells[i_name]
                if not name or name.lower() in ("player", "total"):
                    continue
                a_el = tr.find("a", href=True)
                p_url = urljoin(url, a_el["href"]) if a_el else url

                def num(idx):
                    return to_int(cells[idx]) if (idx is not None and idx < len(cells)) else 0

                records.append(
                    build_european_record(
                        league=league,
                        name=name,
                        team=cells[i_team] if (i_team is not None and i_team < len(cells)) else "",
                        pos_raw=cells[i_pos] if (i_pos is not None and i_pos < len(cells)) else "",
                        class_raw=cells[i_cl] if (i_cl is not None and i_cl < len(cells)) else "",
                        height_raw=cells[i_ht] if (i_ht is not None and i_ht < len(cells)) else "",
                        matches=0, sets=num(i_s), kills=num(i_k), atk_err=num(i_e), atk_att=num(i_ta),
                        aces=num(i_sa), serve_err=0, bs=num(i_bs), ba=num(i_ba),
                        rec_tot=0, rec_err=0, profile_url=p_url, nationality="USA",
                    )
                )
            if records:
                break
        print(f"[{league['name']} ({season})] Scraped {len(records)} players (EU-converted).")
    except Exception as exc:
        print(f"[WARN] NCAA error ({season}): {exc}")
    finally:
        ctx.close()
    return records


def scrape_college_league(browser, league):
    ltype = league.get("type", "")
    if ltype == "usports_ca":
        return scrape_usports_canada(browser, league)
    if ltype == "ncaa_us":
        return scrape_ncaa_mens(browser, league)
    return []
