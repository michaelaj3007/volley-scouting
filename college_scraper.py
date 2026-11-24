import json
import os
import re
import unicodedata
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

ROLE_MAP = {
    "setter": "Setter",
    "s": "Setter",
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
    "mh": "Middle Blocker",
    "libero": "Libero",
    "l": "Libero",
    "ds": "Libero",
    "l/ds": "Libero",
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
        part = part.strip()
        if part in ROLE_MAP:
            return ROLE_MAP[part]
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


def _norm_hdr(text):
    return re.sub(r"\s+", " ", clean(text).lower().replace(".", "")).strip()


def _find_col_idx(headers, candidates):
    norm_hdrs = [_norm_hdr(h) for h in headers]
    # 1) Exact match first (prevents "k" matching "rk", "e" matching "name", "s" matching "pos")
    for cand in candidates:
        c = _norm_hdr(cand)
        for i, h in enumerate(norm_hdrs):
            if h == c:
                return i
    # 2) Substring match ONLY for descriptive multi-character column names (>= 4 chars)
    for cand in candidates:
        c = _norm_hdr(cand)
        if len(c) < 4:
            continue
        for i, h in enumerate(norm_hdrs):
            if c in h:
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


def _fetch_html_pw(ctx, page, url, timeout_ms=30000):
    # Fast authenticated fetch using Playwright's browser context first, fallback to full page render
    try:
        resp = ctx.request.get(url, timeout=timeout_ms)
        if resp.status == 200:
            body = resp.text()
            if "<table" in body.lower():
                return body
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
    path = urlparse(href).path.rstrip("/")
    m = re.search(r"/players/([^/?#]+)", path, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _parse_usports_table(soup, base_url):
    for tbl in soup.find_all("table"):
        header_row = None
        headers = []
        for tr in tbl.find_all("tr")[:4]:
            ths = [clean(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
            if any(_norm_hdr(h) in ("name", "player") for h in ths) and len(ths) >= 4:
                header_row = tr
                headers = ths
                break
        if not header_row:
            continue

        i_name = _find_col_idx(headers, ["name", "player"])
        i_team = _find_col_idx(headers, ["team", "school"])
        i_pos = _find_col_idx(headers, ["pos", "position"])
        i_yr = _find_col_idx(headers, ["yr", "cl", "year", "class", "elig"])
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
        i_bs = _find
