import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

ROLE_MAP = {
    "setter": "Setter", "s": "Setter",
    "opposite": "Opposite", "right side": "Opposite", "rs": "Opposite", "opp": "Opposite", "universal": "Opposite",
    "outside": "Outside Hitter", "outside hitter": "Outside Hitter", "wing spiker": "Outside Hitter",
    "oh": "Outside Hitter", "left side": "Outside Hitter", "ls": "Outside Hitter",
    "middle": "Middle Blocker", "middle blocker": "Middle Blocker", "mb": "Middle Blocker", "mh": "Middle Blocker",
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
VB_CACHE_PATH = "data/volleybox_cache.json"


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def slugify(text):
    norm = unicodedata.normalize("NFKD", str(text or ""))
    ascii_txt = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", ascii_txt.lower()).strip("_") or "unknown"


def _name_tokens(name):
    norm = unicodedata.normalize("NFKD", str(name or ""))
    ascii_txt = "".join(ch for ch in norm if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", ascii_txt.lower())
    return [t for t in cleaned.split() if len(t) >= 2]


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

    m_yr = re.search(r"(?:^|\D)([1-5])(?:\D|$)", c)
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


def _infer_position_from_stats(sets, kills, atk_att, blocks, assists, digs):
    if sets <= 0:
        return "Unknown"
    ast_ps = assists / sets
    blk_ps = blocks / sets
    att_ps = atk_att / sets
    k_ps = kills / sets

    if ast_ps >= 3.5:
        return "Setter"
    if digs > 0 and kills == 0 and blocks == 0 and atk_att <= 2:
        return "Libero"
    if blk_ps >= 0.68 and att_ps < 5.2:
        return "Middle Blocker"
    if k_ps >= 1.0 or att_ps >= 2.5:
        return "Outside Hitter"
    return "Unknown"


def _parse_vb_page(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    pos, height_cm, birth_year, nationality = "", 0, 0, ""

    for dt in soup.find_all(["dt", "th", "td"]):
        lbl = clean(dt.get_text(" ")).rstrip(":").lower()
        sib = dt.find_next_sibling(["dd", "td"])
        if not sib:
            continue
        val = clean(sib.get_text(" "))
        if not val:
            continue
        if lbl in ("position", "positions", "role") and not pos:
            mapped = norm_pos(val)
            if mapped != "Unknown":
                pos = mapped
        elif lbl in ("height", "heigth") and height_cm == 0:
            height_cm = parse_height_to_cm(val)
        elif lbl in ("birthdate", "birth date", "date of birth", "born", "birthday") and birth_year == 0:
            ym = re.search(r"\b(19[7-9]\d|20[0-1]\d)\b", val)
            if ym:
                birth_year = int(ym.group(1))
        elif lbl in ("nationality", "country") and not nationality and len(val) <= 30:
            nationality = val

    lines = [clean(x) for x in soup.get_text("\n").splitlines() if clean(x)]
    for i, line in enumerate(lines):
        low = line.rstrip(":").lower()
        # Look ahead up to 3 lines because Volleybox splits "14 May" and "2004" onto separate lines
        window = " ".join(lines[i + 1 : i + 4])
        same = line.split(":", 1)[1].strip() if ":" in line else ""
        val = same or (lines[i + 1] if i + 1 < len(lines) else "")

        if low in ("position", "positions", "role") and not pos and val:
            mapped = norm_pos(val)
            if mapped != "Unknown":
                pos = mapped
        elif low in ("height", "heigth") and height_cm == 0 and (same or window):
            height_cm = parse_height_to_cm(same or window)
        elif (low in ("birthdate", "birth date", "date of birth", "born", "birthday") or low.startswith("birthdate ")) and birth_year == 0:
            ym = re.search(r"\b(19[7-9]\d|20[0-1]\d)\b", f"{line} {window}")
            if ym:
                birth_year = int(ym.group(1))
        elif low in ("nationality", "country") and not nationality and val and len(val) <= 30:
            nationality = val

    full_txt = " ".join(lines)
    if birth_year == 0:
        m_b = re.search(
            r"(?:birthdate|birth\s*date|date\s*of\s*birth|born)\s*[:\-]?\s*(?:[a-z0-9.,/\-\s]{0,30}?)\b(19[89]\d|200\d|201[0-2])\b",
            full_txt,
            re.IGNORECASE,
        )
        if m_b:
            birth_year = int(m_b.group(1))

    if not pos:
        for k, mapped in (
            ("outside hitter", "Outside Hitter"),
            ("middle blocker", "Middle Blocker"),
            ("opposite", "Opposite"),
            ("setter", "Setter"),
            ("libero", "Libero"),
        ):
            if re.search(rf"\b{k}\b", full_txt, re.IGNORECASE):
                pos = mapped
                break

    return {"position": pos, "height_cm": height_cm, "birth_year": birth_year, "nationality": nationality}


def _lookup_vb_single(name):
    tokens = _name_tokens(name)
    if len(tokens) < 2:
        return {"not_found": True}

    session = requests.Session()
    session.headers.update(HEADERS)
    q = quote_plus(" ".join(tokens))

    candidate_urls = []
    for s_url in (f"https://volleybox.net/players?q={q}", f"https://volleybox.net/search?q={q}"):
        try:
            resp = session.get(s_url, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = urljoin("https://volleybox.net/", a["href"])
                m = re.search(r"https?://(?:www\.)?volleybox\.net/(?:[a-z]{2}/)?([a-z0-9\-]+-p\d+)", href, re.IGNORECASE)
                if not m:
                    continue
                slug = m.group(1).lower()
                if all(tok in slug for tok in tokens):
                    cu = f"https://volleybox.net/{slug}"
                    if cu not in candidate_urls:
                        candidate_urls.append(cu)
            if candidate_urls:
                break
        except Exception:
            continue

    for p_url in candidate_urls[:3]:
        try:
            time.sleep(0.2)
            resp = session.get(p_url, timeout=15)
            if resp.status_code != 200:
                continue
            info = _parse_vb_page(resp.text)
            if info["position"] or info["height_cm"] > 0 or info["birth_year"] > 0 or info["nationality"]:
                return info
        except Exception:
            continue

    return {"not_found": True}


def _enrich_college_records_from_vb(records, stat_hints=None, max_new=350):
    if not records:
        return records

    cache = {}
    if os.path.exists(VB_CACHE_PATH):
        try:
            with open(VB_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}

    to_lookup = {}
    for r in records:
        if r["position"] != "Unknown" and r["height_cm"] > 0 and r["birth_year"] > 0 and r["nationality"]:
            continue
        toks = _name_tokens(r["name"])
        if len(toks) < 2:
            continue
        ck = f"{'_'.join(toks)}_0"
        cached_item = cache.get(ck)
        # Re-check if not cached yet OR if cached earlier without birth_year
        need_refresh = (cached_item is None) or (
            isinstance(cached_item, dict)
            and not cached_item.get("not_found")
            and int(cached_item.get("birth_year", 0) or 0) == 0
        )
        if need_refresh and ck not in to_lookup and len(to_lookup) < max_new:
            to_lookup[ck] = r["name"]

    if to_lookup:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_lookup_vb_single, nm): ck for ck, nm in to_lookup.items()}
            for fut in as_completed(futs):
                ck = futs[fut]
                try:
                    res = fut.result()
                    if res.get("not_found") and ck in cache and not cache[ck].get("not_found"):
                        pass
                    else:
                        cache[ck] = res
                except Exception:
                    pass
        os.makedirs(os.path.dirname(VB_CACHE_PATH), exist_ok=True)
        with open(VB_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    vb_age_hits = 0
    for r in records:
        toks = _name_tokens(r["name"])
        ck = f"{'_'.join(toks)}_0" if len(toks) >= 2 else ""
        info = cache.get(ck) if ck else None
        if info and not info.get("not_found"):
            if r["position"] == "Unknown" and info.get("position"):
                r["position"] = info["position"]
            if r["height_cm"] == 0 and int(info.get("height_cm", 0)) > 0:
                r["height_cm"] = int(info["height_cm"])
            if r["birth_year"] == 0 and int(info.get("birth_year", 0)) > 0:
                by = int(info["birth_year"])
                r["birth_year"] = by
                r["age"] = datetime.now().year - by
                vb_age_hits += 1
            if not r["nationality"] and info.get("nationality"):
                r["nationality"] = info["nationality"]

        if r["position"] == "Unknown" and stat_hints:
            h = stat_hints.get(r["player_id"], {})
            inferred = _infer_position_from_stats(
                sets=r["sets"],
                kills=r["attack_points"],
                atk_att=r["attack_total"],
                blocks=r["blocks"],
                assists=h.get("assists", 0),
                digs=h.get("digs", 0),
            )
            if inferred != "Unknown":
                r["position"] = inferred

    print(f"[College Volleybox] Checked {len(to_lookup)} players, filled {vb_age_hits} birth years/ages from Volleybox.")
    return records


def build_european_record(
    league, name, team, pos_raw, class_raw, height_raw,
    matches, sets, kills, atk_err, atk_att, aces, serve_err,
    bs, ba, rec_tot, rec_err, profile_url,
    raw_pts=0.0, raw_hit_pct=0.0, raw_total_blocks=0.0
):
    season = league.get("season", "2026-27")
    is_can = league["country"] == "Canada"

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
                return _enrich_college_records_from_vb(cached, max_new=350)
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
                i_ast = _find_col_idx(headers, ["assists", "ast"])
                i_dig = _find_col_idx(headers, ["digs", "dig"])

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
                        "aces": 0, "bs": 0, "ba": 0, "btot": 0.0, "raw_pts": 0.0,
                        "assists": 0, "digs": 0, "url": p_url,
                    })
                    for fld, idx in (("pos", i_pos), ("cl", i_cl), ("ht", i_ht)):
                        if idx is not None and idx < len(cells) and cells[idx] not in ("", "-"):
                            entry[fld] = cells[idx]
                    entry["sets"] = max(entry["sets"], gi(i_s))
                    for fld, idx in (("kills", i_k), ("err", i_e), ("att", i_ta), ("aces", i_sa), ("bs", i_bs), ("ba", i_ba), ("assists", i_ast), ("digs", i_dig)):
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

        records = []
        stat_hints = {}
        for e in merged.values():
            rec = build_european_record(
                league=league, name=e["name"], team=e["team"], pos_raw=e["pos"],
                class_raw=e["cl"], height_raw=e["ht"], matches=0, sets=e["sets"],
                kills=e["kills"], atk_err=e["err"], atk_att=e["att"], aces=e["aces"],
                serve_err=0, bs=e["bs"], ba=e["ba"], rec_tot=0, rec_err=0,
                profile_url=e["url"], raw_pts=e["raw_pts"], raw_hit_pct=e["hit_pct"],
                raw_total_blocks=e["btot"],
            )
            stat_hints[rec["player_id"]] = {"assists": e["assists"], "digs": e["digs"]}
            records.append(rec)

        records = _enrich_college_records_from_vb(records, stat_hints=stat_hints, max_new=350)

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
    if ltype == "ncaa_us":
        return scrape_ncaa_mens(browser, league)
    return []
