import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup

CACHE_PATH = "data/volleybox_cache.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

VB_ROLE_MAP = {
    "setter": "Setter",
    "opposite": "Opposite",
    "universal": "Opposite",
    "outside hitter": "Outside Hitter",
    "wing spiker": "Outside Hitter",
    "middle blocker": "Middle Blocker",
    "libero": "Libero",
}


def _clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _strip_accents(text):
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def _name_tokens(name):
    cleaned = re.sub(r"[^a-z0-9\s]", " ", _strip_accents(name).lower())
    return [t for t in cleaned.split() if len(t) >= 2]


def _load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _parse_vb_player_page(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    lines = [_clean(x) for x in soup.get_text("\n").splitlines() if _clean(x)]

    pos = ""
    height_cm = 0
    birth_year = 0
    nationality = ""

    for i, line in enumerate(lines):
        low = line.rstrip(":").lower()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        same = line.split(":", 1)[1].strip() if ":" in line else ""
        val = same or nxt

        if low in ("position", "positions", "role") and not pos and val:
            v_low = val.lower()
            for k, mapped in VB_ROLE_MAP.items():
                if k in v_low:
                    pos = mapped
                    break

        elif low in ("height", "heigth") and height_cm == 0 and val:
            m_h = re.search(r"(1[5-9]\d|2[0-3]\d)", val)
            if m_h:
                height_cm = int(m_h.group(1))

        elif low in ("birthdate", "birth date", "date of birth", "born") and birth_year == 0 and val:
            m_y = re.search(r"(19\d{2}|20[0-1]\d)", val)
            if m_y:
                birth_year = int(m_y.group(1))

        elif low in ("nationality", "country") and not nationality and val and len(val) <= 30:
            nationality = val

    full_txt = " ".join(lines)
    if height_cm == 0:
        m_h = re.search(r"\b(1[7-9]\d|2[0-2]\d)\s*cm\b", full_txt, re.IGNORECASE)
        if m_h:
            height_cm = int(m_h.group(1))

    if not pos:
        for k, mapped in VB_ROLE_MAP.items():
            if re.search(rf"\b{re.escape(k)}\b", full_txt, re.IGNORECASE):
                pos = mapped
                break

    return {
        "position": pos,
        "height_cm": height_cm,
        "birth_year": birth_year,
        "nationality": nationality,
    }


def _lookup_player_on_volleybox(name, birth_year=0):
    tokens = _name_tokens(name)
    if len(tokens) < 2:
        return {"not_found": True}

    session = requests.Session()
    session.headers.update(HEADERS)

    q = quote_plus(" ".join(tokens))
    search_urls = [
        f"https://volleybox.net/players?q={q}",
        f"https://volleybox.net/search?q={q}",
    ]

    candidate_urls = []
    for s_url in search_urls:
        try:
            resp = session.get(s_url, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for a in soup.find_all("a", href=True):
                href = urljoin("https://volleybox.net/", a["href"])
                m = re.search(
                    r"https?://(?:www\.)?volleybox\.net/(?:[a-z]{2}/)?([a-z0-9\-]+-p\d+)",
                    href,
                    re.IGNORECASE,
                )
                if not m:
                    continue
                slug = m.group(1).lower()
                if all(tok in slug for tok in tokens):
                    clean_url = f"https://volleybox.net/{slug}"
                    if clean_url not in candidate_urls:
                        candidate_urls.append(clean_url)
            if candidate_urls:
                break
        except Exception:
            continue

    for p_url in candidate_urls[:3]:
        try:
            time.sleep(0.25)
            resp = session.get(p_url, timeout=15)
            if resp.status_code != 200:
                continue
            info = _parse_vb_player_page(resp.text)
            if birth_year > 0 and info["birth_year"] > 0 and abs(birth_year - info["birth_year"]) > 1:
                continue
            if info["position"] or info["height_cm"] > 0 or info["birth_year"] > 0 or info["nationality"]:
                return info
        except Exception:
            continue

    return {"not_found": True}


def enrich_missing_bios_from_volleybox(df, max_new_lookups=450):
    if df.empty:
        return df

    cache = _load_cache()
    to_lookup = {}

    for _, row in df.iterrows():
        need_pos = str(row.get("position", "")).strip() in ("", "Unknown")
        need_h = int(row.get("height_cm", 0) or 0) == 0
        need_b = int(row.get("birth_year", 0) or 0) == 0
        need_nat = str(row.get("nationality", "")).strip() == ""

        if not (need_pos or need_h or need_b or need_nat):
            continue

        name = str(row.get("name", "")).strip()
        b_year = int(row.get("birth_year", 0) or 0)
        cache_key = f"{'_'.join(_name_tokens(name))}_{b_year}"
        if not cache_key.strip("_0"):
            continue

        if cache_key not in cache and cache_key not in to_lookup and len(to_lookup) < max_new_lookups:
            to_lookup[cache_key] = (name, b_year)

    if to_lookup:
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {
                ex.submit(_lookup_player_on_volleybox, nm, by): ck
                for ck, (nm, by) in to_lookup.items()
            }
            for fut in as_completed(futures):
                ck = futures[fut]
                try:
                    cache[ck] = fut.result()
                except Exception:
                    cache[ck] = {"not_found": True}
        _save_cache(cache)

    updated_rows = 0
    for idx, row in df.iterrows():
        need_pos = str(row.get("position", "")).strip() in ("", "Unknown")
        need_h = int(row.get("height_cm", 0) or 0) == 0
        need_b = int(row.get("birth_year", 0) or 0) == 0
        need_nat = str(row.get("nationality", "")).strip() == ""

        if not (need_pos or need_h or need_b or need_nat):
            continue

        name = str(row.get("name", "")).strip()
        b_year = int(row.get("birth_year", 0) or 0)
        cache_key = f"{'_'.join(_name_tokens(name))}_{b_year}"
        info = cache.get(cache_key)
        if not info or info.get("not_found"):
            continue

        changed = False
        if need_pos and info.get("position"):
            df.at[idx, "position"] = info["position"]
            changed = True
        if need_h and int(info.get("height_cm", 0)) > 0:
            df.at[idx, "height_cm"] = int(info["height_cm"])
            changed = True
        if need_b and int(info.get("birth_year", 0)) > 0:
            by = int(info["birth_year"])
            df.at[idx, "birth_year"] = by
            df.at[idx, "age"] = datetime.now().year - by
            changed = True
        if need_nat and info.get("nationality"):
            df.at[idx, "nationality"] = info["nationality"]
            changed = True
        if changed:
            updated_rows += 1

    print(f"[Volleybox] Checked {len(to_lookup)} new players, enriched {updated_rows} profiles across all leagues (cache: {len(cache)}).")
    return df
