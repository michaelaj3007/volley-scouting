import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}

DEFAULT_CVF_SUBIDS = [
    "body_celkem",
    "body_sety",
    "eso_celkem",
    "eso_sety",
    "podani_uspesnost",
    "blok_celkem",
    "blok_sety",
    "utok_celkem",
    "utok_uspesnost",
    "prijem_uspesnost",
]


def clean(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def strip_accents(text):
    norm = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def to_float(val):
    s = re.sub(r"[^0-9.\-]", "", str(val or "").strip().replace("%", "").replace(",", "."))
    try:
        return float(s) if s not in ("", "-", ".") else 0.0
    except ValueError:
        return 0.0


def to_int(val):
    return int(round(to_float(val)))


def make_key(name, href):
    if href:
        qs = parse_qs(urlparse(href).query)
        pid_vals = qs.get("id") or qs.get("ID")
        if pid_vals and pid_vals[0].isdigit():
            return pid_vals[0]
    slug = re.sub(r"[^a-z0-9]+", "_", strip_accents(clean(name)).lower()).strip("_")
    return slug or "unknown"


def find_item_el(sn):
    el = sn
    for _ in range(5):
        el = el.parent
        if not el:
            break
        if "list__item" in (el.get("class") or []):
            return el
    el = sn
    while el and el.parent:
        if len(el.parent.select(".list__item__surname")) > 1:
            return el
        el = el.parent
    return None


def extract_sections(soup, page_url):
    groups = {}
    order = []
    for sn in soup.select(".list__item__surname"):
        item = find_item_el(sn)
        if not item or not item.parent:
            continue
        lp = item.parent
        key = id(lp)
        if key not in groups:
            groups[key] = (lp, [])
            order.append(key)
        if item not in groups[key][1]:
            groups[key][1].append(item)

    page_head = " | ".join(
        clean(h.get_text(" ")) for h in soup.select("h1, h2")
        if not h.find_parent(class_="list__item") and clean(h.get_text(" "))
    )

    sections = []
    for key in order:
        lp, items = groups[key]
        my_cnt = len(lp.select(".list__item__surname"))
        box = lp
        for _ in range(4):
            p = box.parent
            if not p or len(p.select(".list__item__surname")) != my_cnt:
                break
            box = p

        heads = [
            clean(h.get_text(" ")) for h in box.select("h1, h2, h3, h4, [class*='head'], [class*='title']")
            if not lp.find(lambda tag: tag is h) and clean(h.get_text(" ")) and strip_accents(clean(h.get_text(" "))).lower() != "dalsi"
        ]
        title = " | ".join(dict.fromkeys(heads)) or page_head

        parsed_items = []
        for item in items:
            main_info = item.select_one(".list__item__main__info") or item
            sn_el = item.select_one(".list__item__surname")
            name_el = item.select_one(".list__item__highlighted, h3") or sn_el
            surname = clean(sn_el.get_text(" ")) if sn_el else ""
            full_name = clean(name_el.get_text(" ")) if name_el else surname
            if not full_name:
                continue

            if item.name == "a" and item.get("href"):
                href = urljoin(page_url, item["href"])
            else:
                a_el = item.find("a", href=re.compile(r"/hrac/", re.IGNORECASE)) or item.find("a", href=True)
                href = urljoin(page_url, a_el["href"]) if a_el else page_url

            team = ""
            for img in main_info.find_all("img"):
                lbl = clean(img.get("alt") or img.get("title") or "")
                if lbl and surname.lower() not in lbl.lower():
                    team = lbl
                    break

            photo = ""
            for img in item.find_all("img"):
                src = img.get("src") or ""
                if src and not re.search(r"no[-_]?photo|placeholder|default|logo|club|team", src, re.IGNORECASE):
                    photo = urljoin(page_url, src)
                    break

            full_txt = clean(item.get_text(" "))
            info_txt = clean(main_info.get_text(" "))
            raw_stats = clean(full_txt.replace(info_txt, "", 1))
            raw_stats = re.sub(r"^\d+\.\s*", "", raw_stats).strip()
            parsed_items.append({"name": full_name, "team": team, "href": href, "photo": photo, "stats": raw_stats})

        sections.append({"title": title, "items": parsed_items})
    return sections


def apply_section(merged, league, title, items, src_url):
    t = strip_accents(f"{title} {src_url}").lower()
    is_serve = any(k in t for k in ["podav", "podan", "esa", "eso", "serv"])
    is_block = "blok" in t or "block" in t
    is_attack = any(k in t for k in ["utoc", "utok", "smec", "att"])
    is_rec = any(k in t for k in ["prij", "recep"])
    is_points = any(k in t for k in ["boduj", "celkem bod", "body/set", "nejvice bod", "id=body"])
    has_sets = "set" in t and "pokus" not in t

    if not (is_serve or is_block or is_attack or is_rec or is_points):
        return

    for it in items:
        name = it["name"]
        href = it["href"]
        fid = f"{league['country']}_{league['competition_id']}_{make_key(name, href)}"
        if fid not in merged:
            merged[fid] = {
                "player_id": fid, "league": league["name"], "country": league["country"], "gender": league.get("gender", ""),
                "name": name, "team": it["team"], "position": "Unknown", "raw_position": "",
                "height_cm": 0, "birth_year": 0, "age": 0, "nationality": "CZE",
                "matches": 0, "sets": 0, "points": 0, "points_per_set": 0.0, "break_points": 0, "won_lost": 0,
                "serve_total": 0, "aces": 0, "aces_per_set": 0.0, "serve_errors": 0,
                "blocks": 0, "blocks_per_set": 0.0,
                "attack_total": 0, "attack_points": 0, "attack_errors": 0, "attack_blocked": 0, "attack_pct": 0.0, "attack_eff_pct": 0.0,
                "reception_total": 0, "reception_pos_pct": 0.0, "reception_perf_pct": 0.0,
                "profile_url": href, "photo_url": it["photo"],
            }
        rec = merged[fid]
        if not rec["team"] and it["team"]:
            rec["team"] = it["team"]
        if not rec["photo_url"] and it["photo"]:
            rec["photo_url"] = it["photo"]
        if "/hrac/" in href.lower():
            rec["profile_url"] = href

        raw = it["stats"]
        m_frac = re.search(r"(\d+)\s*/\s*(\d+)", raw)
        f_num, f_den = (int(m_frac.group(1)), int(m_frac.group(2))) if m_frac else (0, 0)
        rest = (raw[:m_frac.start()] + " " + raw[m_frac.end():]) if m_frac else raw
        m_val = re.search(r"(-?\d+(?:[.,]\d+)?)\s*(%?)", rest)
        val = to_float(m_val.group(1)) if m_val else 0.0
        is_pct = (m_val.group(2) == "%") if m_val else False

        if is_serve:
            rec["aces"] = max(rec["aces"], f_num if f_num else (0 if (is_pct or has_sets) else to_int(val)))
            if has_sets and not is_pct:
                rec["sets"] = max(rec["sets"], f_den)
            elif is_pct or "pokus" in t:
                rec["serve_total"] = max(rec["serve_total"], f_den)
        elif is_block:
            rec["blocks"] = max(rec["blocks"], f_num if f_num else (0 if (is_pct or has_sets) else to_int(val)))
            if has_sets or not is_pct:
                rec["sets"] = max(rec["sets"], f_den)
        elif is_attack:
            if is_pct or "%" in t:
                rec["attack_eff_pct" if "efekt" in t else "attack_pct"] = round(val, 1)
                rec["attack_points"] = max(rec["attack_points"], f_num)
                rec["attack_total"] = max(rec["attack_total"], f_den)
            else:
                rec["attack_points"] = max(rec["attack_points"], f_num or to_int(val))
                if has_sets:
                    rec["sets"] = max(rec["sets"], f_den)
                else:
                    rec["attack_total"] = max(rec["attack_total"], f_den)
        elif is_rec:
            if any(k in t for k in ["excel", "perf", "vyborn"]):
                rec["reception_perf_pct"] = round(val, 1)
            else:
                rec["reception_pos_pct"] = round(val, 1)
            rec["reception_total"] = max(rec["reception_total"], f_den)
        elif is_points:
            rec["points"] = max(rec["points"], f_num or to_int(val))
            if not is_pct and "pokus" not in t:
                rec["sets"] = max(rec["sets"], f_den)


def enrich_cvf_birth_and_team(session, rec):
    url = rec.get("profile_url", "")
    if not url or "/hrac/" not in url.lower():
        return rec
    try:
        resp = session.get(url, timeout=18)
        if resp.status_code != 200:
            return rec
        soup = BeautifulSoup(resp.text, "lxml")
        if not rec["team"]:
            ta = soup.find("a", href=re.compile(r"/tym|/druzstvo|/klub", re.IGNORECASE))
            if ta:
                rec["team"] = clean(ta.get_text(" "))
        txt = clean(soup.get_text(" "))
        ym = re.search(r"\b(19[7-9]\d|20[0-1]\d)\b", txt)
        if ym and rec["birth_year"] == 0:
            rec["birth_year"] = int(ym.group(1))
            rec["age"] = datetime.now().year - rec["birth_year"]
    except Exception:
        pass
    return rec


def scrape_cvf_cz_league(browser, session, league):
    entry_url = league["url"].rstrip("/") + "/"
    merged = {}
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = ctx.new_page()
    try:
        print(f"[{league['name']}] Opening {entry_url}")
        page.goto(entry_url, wait_until="networkidle", timeout=45000)
        page.wait_for_selector(".list__item__surname", timeout=15000)

        soup_main = BeautifulSoup(page.content(), "lxml")
        for sec in extract_sections(soup_main, entry_url):
            apply_section(merged, league, sec["title"], sec["items"], entry_url)

        # Collect all DALŠÍ links (note: singular 'zebricek' in CVF URLs!)
        sub_urls = []
        for a in soup_main.find_all("a", href=True):
            full_href = urljoin(entry_url, a["href"])
            if "/zebricek/" in full_href.lower() and full_href not in sub_urls:
                sub_urls.append(full_href)

        base_cat = entry_url.replace("/zebricky/", "/zebricek/")
        for sub_id in DEFAULT_CVF_SUBIDS:
            cand = f"{base_cat}?id={sub_id}"
            if cand not in sub_urls:
                sub_urls.append(cand)

        print(f"[DEBUG][CVF] Visiting {len(sub_urls)} full ranking pages...")
        for s_url in sub_urls[:14]:
            try:
                resp = page.goto(s_url, wait_until="networkidle", timeout=30000)
                if resp and resp.status >= 400:
                    continue
                page.wait_for_selector(".list__item__surname", timeout=8000)
            except Exception:
                continue
            for sec in extract_sections(BeautifulSoup(page.content(), "lxml"), s_url):
                apply_section(merged, league, sec["title"], sec["items"], s_url)

        records = list(merged.values())
        for r in records:
            s = r["sets"]
            r["points_per_set"] = round(r["points"] / s, 2) if s > 0 else 0.0
            r["aces_per_set"] = round(r["aces"] / s, 2) if s > 0 else 0.0
            r["blocks_per_set"] = round(r["blocks"] / s, 2) if s > 0 else 0.0
            if r["attack_pct"] == 0.0 and r["attack_total"] > 0 and r["attack_points"] > 0:
                r["attack_pct"] = round(100.0 * r["attack_points"] / r["attack_total"], 1)

        if records:
            with ThreadPoolExecutor(max_workers=10) as ex:
                records = [f.result() for f in as_completed([ex.submit(enrich_cvf_birth_and_team, session, r) for r in records])]
            top = max(records, key=lambda x: x["points"])
            print(f"[{league['name']}] Scraped {len(records)} Czech players! Top: {top['name']} ({top['points']} pts / {top['sets']} sets)")
    except Exception as exc:
        print(f"[WARN] CVF error: {exc}")
        records = list(merged.values())
    finally:
        ctx.close()
    return records
