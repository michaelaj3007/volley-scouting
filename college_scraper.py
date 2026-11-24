import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
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

NCAA_COM_CATEGORIES = [
    ("points", "https://www.ncaa.com/stats/volleyball-men/d1/current/individual/755"),
    ("kills", "https://www.ncaa.com/stats/volleyball-men/d1/current/individual/513"),
    ("blocks", "https://www.ncaa.com/stats/volleyball-men/d1/current/individual/516"),
    ("aces", "https://www.ncaa.com/stats/volleyball-men/d1/current/individual/515"),
    ("hitting", "https://www.ncaa.com/stats/volleyball-men/d1/current/individual/512"),
]


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
    m_ft = re.search(r"([5-7])\s*(?:ft\.?|feet|'|′|-)\s*(\d{1,2})", s, re.IGNORECASE)
    if m_ft:
        ft, inch = int(m_ft.group(1)), int(m_ft.group(2))
        if 0 <= inch <= 11:
            cm = int(round((ft * 12 + inch) * 2.54))
            return cm if 150 <= cm <= 235 else 0
    m_meters = re.search(r"\b([12])[.,](\d{2})\s*m?\b", s, re.IGNORECASE)
    if m_meters:
        cm = int(m_meters.group(1)) * 100 + int(m_meters.group(2))
        return cm if 150 <= cm <= 235 else 0
    cm = to_int(s)
    return cm if 150 <= cm <= 235 else 0


def build_european_record(
    league, name, team, pos_raw, class_raw, height_raw,
    matches, sets, kills, atk_err, atk_att, aces, serve_err,
    bs, ba, rec_tot, rec_err, profile_url
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
        "nationality": "",
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


def _enrich_usports_player_page(item):
    url = item.get("profile_url", "")
    if not url or "/players/" not in url.lower():
        return item

    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        resp = session.get(url, timeout=18)
        if resp.status_code != 200:
            return item
        soup = BeautifulSoup(resp.text, "lxml")

        # Full player name from heading (instead of abbreviated "B Drover")
        for h in soup.select("h1, h2, .player-name, [class*='player-title']"):
            txt = clean(h.get_text(" "))
            if txt and len(txt.split()) >= 2 and "u sports" not in txt.lower() and "volleyball" not in txt.lower():
                item["name"] = re.sub(r"^#?\d+\s+", "", txt).strip()
                break

        lines = [clean(x) for x in soup.get_text("\n").splitlines() if clean(x)]
        for i, line in enumerate(lines):
            low = line.rstrip(":").lower()
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            same = line.split(":", 1)[1].strip() if ":" in line else ""
            val = same or nxt
            if not val:
                continue

            if low in ("year", "yr", "class", "eligibility", "academic year") and not item["class_year"]:
                item["class_year"] = val
            elif low in ("position", "pos") and not item["raw_position"]:
                item["raw_position"] = val
            elif low in ("height", "ht") and not item["height_raw"]:
                item["height_raw"] = val
            elif low in ("team", "school", "university") and not item["team"]:
                item["team"] = val

        # Check season stats table on player profile for exact Aces, Solo Blocks, Block Assists, Kills
        for tbl in soup.find_all("table"):
            ths = [clean(c.get_text(" ")).lower() for c in tbl.find_all("th")]
            if not any(h in ("s", "sp", "k", "sa", "bs", "ba") for h in ths):
                continue
            i_s = _find_col_idx(ths, ["sp", "s"])
            i_k = _find_col_idx(ths, ["k"])
            i_e = _find_col_idx(ths, ["e"])
            i_ta = _find_col_idx(ths, ["ta"])
            i_sa = _find_col_idx(ths, ["sa"])
            i_se = _find_col_idx(ths, ["se"])
            i_bs = _find_col_idx(ths, ["bs"])
            i_ba = _find_col_idx(ths, ["ba"])
            i_re = _find_col_idx(ths, ["re"])

            for tr in tbl.find_all("tr"):
                row_txt = clean(tr.get_text(" ")).lower()
                if "total" in row_txt or "career" in row_txt or item["season"] in row_txt:
                    cs = [clean(td.get_text(" ")) for td in tr.find_all("td")]
                    if len(cs) < 5:
                        continue
                    def g(idx):
                        return to_int(cs[idx]) if (idx is not None and idx < len(cs)) else 0
                    if g(i_s) > 0:
                        item["sets"] = max(item["sets"], g(i_s))
                    if g(i_k) > 0:
                        item["kills"] = max(item["kills"], g(i_k))
                    if g(i_e) > 0:
                        item["atk_err"] = max(item["atk_err"], g(i_e))
                    if g(i_ta) > 0:
                        item["atk_att"] = max(item["atk_att"], g(i_ta))
                    if g(i_sa) > 0:
                        item["aces"] = max(item["aces"], g(i_sa))
                    if g(i_se) > 0:
                        item["serve_err"] = max(item["serve_err"], g(i_se))
                    if g(i_bs) > 0:
                        item["bs"] = max(item["bs"], g(i_bs))
                    if g(i_ba) > 0:
                        item["ba"] = max(item["ba"], g(i_ba))
                    if g(i_re) > 0:
                        item["rec_err"] = max(item["rec_err"], g(i_re))
                    if item["season"] in row_txt:
                        break
    except Exception:
        pass
    return item


def scrape_usports_canada(browser, league):
    season = league.get("season", "2026-27")
    candidate_urls = [
        f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&view=full&pos=all",
        f"https://en.usports.ca/sports/mvball/{season}/players?sort=pts&view=lineup&pos=all",
        league.get("url"),
    ]
    candidate_urls = [u for i, u in enumerate(candidate_urls) if u and u not in candidate_urls[:i]]

    raw_items = []
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        for url in candidate_urls:
            print(f"[{league['name']} ({season})] Opening {url}")
            try:
                resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if resp and resp.status >= 400:
                    continue
                page.wait_for_selector("table", state="attached", timeout=15000)
                page.wait_for_timeout(1500)
            except Exception:
                continue

            soup = BeautifulSoup(page.content(), "lxml")
            target_table = None
            headers = []
            for tbl in soup.find_all("table"):
                for r in tbl.find_all("tr")[:3]:
                    ths = [clean(c.get_text(" ")).lower() for c in r.find_all(["th", "td"])]
                    if any("name" in h or "player" in h for h in ths) and any(
                        h in ("k", "kills", "sp", "s", "pts", "gp") for h in ths
                    ):
                        target_table = tbl
                        headers = ths
                        break
                if target_table:
                    break

            if not target_table:
                continue

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
                if len(cells) < 5 or i_name is None or i_name >= len(cells):
                    continue
                name = cells[i_name]
                if not name or name.lower() in ("total", "totals", "team", "name", "player"):
                    continue

                a_el = tr.find("a", href=True)
                p_url = urljoin(url, a_el["href"]) if a_el else url

                def num(idx):
                    return to_int(cells[idx]) if (idx is not None and idx < len(cells)) else 0

                raw_items.append({
                    "season": season,
                    "name": name,
                    "team": cells[i_team] if (i_team is not None and i_team < len(cells)) else "",
                    "raw_position": cells[i_pos] if (i_pos is not None and i_pos < len(cells)) else "",
                    "class_year": cells[i_yr] if (i_yr is not None and i_yr < len(cells)) else "",
                    "height_raw": cells[i_ht] if (i_ht is not None and i_ht < len(cells)) else "",
                    "matches": num(i_mp),
                    "sets": num(i_sp),
                    "kills": num(i_k),
                    "atk_err": num(i_e),
                    "atk_att": num(i_ta),
                    "aces": num(i_sa),
                    "serve_err": num(i_se),
                    "bs": num(i_bs),
                    "ba": num(i_ba),
                    "rec_err": num(i_re),
                    "profile_url": p_url,
                })
            if raw_items:
                break
    finally:
        ctx.close()

    if raw_items:
        with ThreadPoolExecutor(max_workers=10) as ex:
            raw_items = [f.result() for f in as_completed([ex.submit(_enrich_usports_player_page, it) for it in raw_items])]

    records = [
        build_european_record(
            league=league, name=it["name"], team=it["team"], pos_raw=it["raw_position"],
            class_raw=it["class_year"], height_raw=it["height_raw"], matches=it["matches"],
            sets=it["sets"], kills=it["kills"], atk_err=it["atk_err"], atk_att=it["atk_att"],
            aces=it["aces"], serve_err=it["serve_err"], bs=it["bs"], ba=it["ba"],
            rec_tot=0, rec_err=it["rec_err"], profile_url=it["profile_url"],
        )
        for it in raw_items
    ]
    print(f"[{league['name']} ({season})] Scraped {len(records)} players with full profiles (EU-converted).")
    return records


def scrape_ncaa_mens(browser, league):
    season = league.get("season", "2026-27")
    if season != "2025-26":
        print(f"[{league['name']} ({season})] NCAA D1 season starts in January 2027 (0 games played yet).")
        return []

    merged = {}
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        for cat, base_cat_url in NCAA_COM_CATEGORIES:
            for page_no in range(1, 5):
                url = base_cat_url if page_no == 1 else f"{base_cat_url}/p{page_no}"
                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=35000)
                    if resp and resp.status >= 400:
                        break
                    page.wait_for_selector("table", state="attached", timeout=10000)
                    page.wait_for_timeout(800)
                except Exception:
                    break

                soup = BeautifulSoup(page.content(), "lxml")
                tbl = soup.find("table")
                if not tbl:
                    break

                headers = [clean(th.get_text(" ")).lower() for th in tbl.find_all("th")]
                i_name = _find_col_idx(headers, ["name", "player"])
                i_team = _find_col_idx(headers, ["team"])
                i_cl = _find_col_idx(headers, ["cl", "class", "yr"])
                i_pos = _find_col_idx(headers, ["pos"])
                i_ht = _find_col_idx(headers, ["ht"])
                i_s = _find_col_idx(headers, ["s", "sets"])
                i_k = _find_col_idx(headers, ["kills", "k"])
                i_e = _find_col_idx(headers, ["errors", "e"])
                i_ta = _find_col_idx(headers, ["total attacks", "ta", "att"])
                i_sa = _find_col_idx(headers, ["aces", "sa"])
                i_bs = _find_col_idx(headers, ["block solos", "bs"])
                i_ba = _find_col_idx(headers, ["block assists", "ba"])
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

                    def num(idx):
                        return to_int(cells[idx]) if (idx is not None and idx < len(cells)) else 0

                    entry = merged.setdefault(
                        key,
                        {
                            "name": name, "team": team, "pos": "", "cl": "", "ht": "",
                            "sets": 0, "kills": 0, "err": 0, "att": 0, "aces": 0,
                            "bs": 0, "ba": 0, "raw_pts": 0, "url": p_url,
                        },
                    )
                    if i_pos is not None and i_pos < len(cells) and cells[i_pos] not in ("", "-"):
                        entry["pos"] = cells[i_pos]
                    if i_cl is not None and i_cl < len(cells) and cells[i_cl] not in ("", "-"):
                        entry["cl"] = cells[i_cl]
                    if i_ht is not None and i_ht < len(cells) and cells[i_ht] not in ("", "-"):
                        entry["ht"] = cells[i_ht]

                    entry["sets"] = max(entry["sets"], num(i_s))
                    if i_k is not None:
                        entry["kills"] = max(entry["kills"], num(i_k))
                    if i_e is not None:
                        entry["err"] = max(entry["err"], num(i_e))
                    if i_ta is not None:
                        entry["att"] = max(entry["att"], num(i_ta))
                    if i_sa is not None:
                        entry["aces"] = max(entry["aces"], num(i_sa))
                    if i_bs is not None:
                        entry["bs"] = max(entry["bs"], num(i_bs))
                    if i_ba is not None:
                        entry["ba"] = max(entry["ba"], num(i_ba))
                    if i_pts is not None:
                        entry["raw_pts"] = max(entry["raw_pts"], num(i_pts))
                    rows_added += 1

                if rows_added == 0:
                    break
            print(f"[DEBUG][NCAA] {cat}: total unique players so far = {len(merged)}")

        records = []
        for e in merged.values():
            rec = build_european_record(
                league=league, name=e["name"], team=e["team"], pos_raw=e["pos"],
                class_raw=e["cl"], height_raw=e["ht"], matches=0, sets=e["sets"],
                kills=e["kills"], atk_err=e["err"], atk_att=e["att"], aces=e["aces"],
                serve_err=0, bs=e["bs"], ba=e["ba"], rec_tot=0, rec_err=0,
                profile_url=e["url"],
            )
            if rec["points"] == 0 and e["raw_pts"] > 0:
                rec["points"] = e["raw_pts"]
                rec["points_per_set"] = round(e["raw_pts"] / e["sets"], 2) if e["sets"] > 0 else 0.0
            records.append(rec)

        print(f"[{league['name']} ({season})] Scraped {len(records)} players (EU-converted).")
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
