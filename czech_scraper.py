import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
}

CZ_ROLE_MAP = {
    "nahrávač": "Setter",
    "nahravac": "Setter",
    "setter": "Setter",
    "univerzál": "Opposite",
    "univerzal": "Opposite",
    "opposite": "Opposite",
    "smečař": "Outside Hitter",
    "smecar": "Outside Hitter",
    "outside": "Outside Hitter",
    "blokař": "Middle Blocker",
    "blokar": "Middle Blocker",
    "middle": "Middle Blocker",
    "libero": "Libero",
}


def clean_text(text):
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


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


def normalize_header(h):
    return re.sub(r"\s+", " ", clean_text(h).lower())


def normalize_cz_position(raw):
    low = clean_text(raw).lower()
    for k, v in CZ_ROLE_MAP.items():
        if k in low:
            return v
    return clean_text(raw) if clean_text(raw) else "Unknown"


def find_col(headers, candidates):
    for cand in candidates:
        c_norm = normalize_header(cand)
        c_ns = c_norm.replace(" ", "")
        for i, h in enumerate(headers):
            if h == c_norm or h.replace(" ", "") == c_ns:
                return i
    return None


def cell_val(cells, idx, default=""):
    if idx is not None and 0 <= idx < len(cells):
        return cells[idx]
    return default


def get_table_title(table):
    parts = []
    prev = table.find_previous(["h1", "h2", "h3", "h4", "caption"])
    if prev:
        parts.append(normalize_header(prev.get_text()))
    card = table.find_parent(class_=re.compile(r"card|panel|box|block|section|tab-pane", re.IGNORECASE))
    if card:
        hdr = card.find(class_=re.compile(r"header|title|heading", re.IGNORECASE))
        if hdr:
            parts.append(normalize_header(hdr.get_text()))
    return " | ".join(dict.fromkeys(p for p in parts if p))


def parse_cvf_table(table, league, page_url):
    base = league["base_url"].rstrip("/")
    rows = table.find_all("tr")
    if len(rows) < 2:
        return []

    ctx_title = get_table_title(table)
    header_row_idx = None
    headers = []

    stat_keywords = [
        "hráč", "hrac", "příjmení", "prijmeni", "jméno", "jmeno", "player",
        "klub", "družstvo", "druzstvo", "tým", "tym", "sety", "body", "esa",
        "bloky", "blok", "útok", "utok", "příjem", "prijem", "záp", "zap", "poř", "por",
    ]
    for r_i, tr in enumerate(rows[:4]):
        h_cells = [normalize_header(c.get_text()) for c in tr.find_all(["th", "td"])]
        if len(h_cells) >= 3 and any(k in " ".join(h_cells) for k in stat_keywords):
            header_row_idx = r_i
            headers = h_cells
            break

    data_start_idx = (header_row_idx + 1) if header_row_idx is not None else 0
    if header_row_idx is None:
        has_links = any(tr.find("a", href=re.compile(r"/hrac|player", re.IGNORECASE)) for tr in rows[:6])
        if not has_links:
            return []
        first_cells = [clean_text(c.get_text()) for c in rows[0].find_all(["td", "th"])]
        headers = [f"col_{i}" for i in range(len(first_cells))]

    print(f"[DEBUG][CVF] Title='{ctx_title}' | Headers={headers}")

    i_name = find_col(headers, ["hráč", "hrac", "player", "příjmení a jméno", "prijmeni a jmeno"])
    i_surname = find_col(headers, ["příjmení", "prijmeni", "surname"])
    i_firstname = find_col(headers, ["jméno", "jmeno", "name"])
    i_team = find_col(headers, ["družstvo", "druzstvo", "tým", "tym", "klub", "team"])
    i_pos = find_col(headers, ["post", "pozice", "role", "position"])
    i_height = find_col(headers, ["výška", "vyska", "height"])
    i_birth = find_col(headers, ["ročník", "rocnik", "nar.", "narození", "datum narození", "birth"])
    i_matches = find_col(headers, ["záp.", "zap.", "z", "utk.", "utkání", "matches"])
    i_sets = find_col(headers, ["sety", "s", "sets"])

    sample_trs = rows[data_start_idx : data_start_idx + 5]
    if i_name is None and i_surname is None:
        link_hits = {}
        text_hits = {}
        for tr in sample_trs:
            for c_i, td in enumerate(tr.find_all(["td", "th"])):
                txt = clean_text(td.get_text())
                if not txt or re.fullmatch(r"[\d.,%+\- ]+", txt):
                    continue
                a = td.find("a", href=True)
                if a and ("/hrac" in a["href"].lower() or "player" in a["href"].lower()):
                    link_hits[c_i] = link_hits.get(c_i, 0) + 1
                elif len(txt) >= 4:
                    text_hits[c_i] = text_hits.get(c_i, 0) + 1
        if link_hits:
            i_name = max(link_hits, key=link_hits.get)
        elif text_hits:
            i_name = sorted(text_hits.keys())[0]

    if i_team is None:
        for tr in sample_trs:
            for c_i, td in enumerate(tr.find_all(["td", "th"])):
                if c_i == i_name:
                    continue
                a = td.find("a", href=True)
                if a and any(k in a["href"].lower() for k in ["/druzstvo", "/tym", "/klub", "team"]):
                    i_team = c_i
                    break
            if i_team is not None:
                break

    is_block = "blok" in ctx_title
    is_serve = "podán" in ctx_title or "podan" in ctx_title or "esa" in ctx_title
    is_attack = "útok" in ctx_title or "utok" in ctx_title or "smeč" in ctx_title
    is_rec = "příj" in ctx_title or "prij" in ctx_title

    i_pts = None if (is_block or is_serve or is_attack or is_rec) else find_col(headers, ["body", "b", "pts", "points"])
    i_aces = find_col(headers, ["esa", "eso", "podání #", "podani #", "aces"])
    i_s_tot = find_col(headers, ["podání celk", "podani celk", "podání", "podani"])
    i_blk = find_col(headers, ["bloky", "blok", "blok #", "blocks"])
    i_atk_pts = find_col(headers, ["útok #", "utok #", "útok body", "utok body"])
    i_atk_tot = find_col(headers, ["útok celk", "utok celk", "útok", "utok"])
    i_atk_pct = find_col(headers, ["útok %", "utok %", "úsp. útoku", "usp. utoku", "úsp.%", "usp.%"])
    i_rec_tot = find_col(headers, ["příjem celk", "prijem celk", "příjem", "prijem"])
    i_rec_pct = find_col(headers, ["příjem %", "prijem %", "poz. příjem", "poz. prijem", "poz.%"])
    i_rec_perf = find_col(headers, ["perf. příjem", "perf. prijem", "výb.%", "vyb.%"])

    if is_block and i_blk is None:
        i_blk = find_col(headers, ["body", "b", "#", "celkem"])
    if is_serve and i_aces is None:
        i_aces = find_col(headers, ["body", "b", "#"])
    if is_attack:
        if i_atk_pts is None:
            i_atk_pts = find_col(headers, ["body", "b", "#"])
        if i_atk_pct is None:
            i_atk_pct = find_col(headers, ["%", "úsp%", "úsp.%"])
    if is_rec and i_rec_pct is None:
        i_rec_pct = find_col(headers, ["%", "poz%", "poz.%"])

    if not any(x is not None for x in [i_pts, i_aces, i_blk, i_atk_pts, i_atk_pct, i_rec_pct]):
        skip = {c for c in [0, i_name, i_surname, i_firstname, i_team, i_matches, i_sets] if c is not None}
        for c_i in range(len(headers)):
            if c_i in skip:
                continue
            vals = [cell_val([clean_text(x.get_text()) for x in tr.find_all(["td", "th"])], c_i) for tr in sample_trs]
            if any(re.search(r"\d", v) for v in vals):
                has_pct = any("%" in v or "," in v or "." in v for v in vals)
                if is_block:
                    i_blk = c_i
                elif is_serve:
                    i_aces = c_i
                elif is_attack:
                    if has_pct:
                        i_atk_pct = c_i
                    else:
                        i_atk_pts = c_i
                elif is_rec:
                    i_rec_pct = c_i
                else:
                    i_pts = c_i
                break

    records = []
    for tr in rows[data_start_idx:]:
        cells = [clean_text(c.get_text()) for c in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue

        if i_surname is not None and i_firstname is not None:
            p_name = f"{cell_val(cells, i_surname)} {cell_val(cells, i_firstname)}".strip()
        elif i_name is not None:
            p_name = cell_val(cells, i_name)
        else:
            p_name = cell_val(cells, i_surname)

        if not p_name or p_name.lower() in ("celkem", "total", "hráč", "hrac", "příjmení", "prijmeni"):
            continue

        team_name = cell_val(cells, i_team)
        link = tr.find("a", href=re.compile(r"/hrac|player", re.IGNORECASE)) or tr.find("a", href=True)
        p_url = urljoin(base + "/", link["href"]) if link else page_url
        m_id = re.search(r"(\d{2,})", p_url)
        pl_id = m_id.group(1) if m_id else re.sub(r"\W+", "_", f"{p_name}_{team_name}".lower())

        sets = to_int(cell_val(cells, i_sets))
        pts = to_int(cell_val(cells, i_pts))
        aces = to_int(cell_val(cells, i_aces))
        blocks = to_int(cell_val(cells, i_blk))
        h_cm = to_int(cell_val(cells, i_height))
        if h_cm < 140 or h_cm > 235:
            h_cm = 0

        ym = re.search(r"(19\d{2}|20[0-1]\d)", cell_val(cells, i_birth))
        b_year = int(ym.group(1)) if ym else 0
        age = (datetime.now().year - b_year) if b_year else 0
        raw_pos = cell_val(cells, i_pos)

        records.append({
            "player_id": f"{league['country']}_{league['competition_id']}_{pl_id}",
            "league": league["name"],
            "country": league["country"],
            "gender": league.get("gender", ""),
            "name": p_name,
            "team": team_name,
            "position": normalize_cz_position(raw_pos),
            "raw_position": raw_pos,
            "height_cm": h_cm,
            "birth_year": b_year,
            "age": age,
            "nationality": "CZE",
            "matches": to_int(cell_val(cells, i_matches)),
            "sets": sets,
            "points": pts,
            "points_per_set": round(pts / sets, 2) if sets > 0 else 0.0,
            "break_points": 0,
            "won_lost": 0,
            "serve_total": to_int(cell_val(cells, i_s_tot)),
            "aces": aces,
            "aces_per_set": round(aces / sets, 2) if sets > 0 else 0.0,
            "serve_errors": 0,
            "blocks": blocks,
            "blocks_per_set": round(blocks / sets, 2) if sets > 0 else 0.0,
            "attack_total": to_int(cell_val(cells, i_atk_tot)),
            "attack_points": to_int(cell_val(cells, i_atk_pts)),
            "attack_errors": 0,
            "attack_blocked": 0,
            "attack_pct": to_float(cell_val(cells, i_atk_pct)),
            "attack_eff_pct": 0.0,
            "reception_total": to_int(cell_val(cells, i_rec_tot)),
            "reception_pos_pct": to_float(cell_val(cells, i_rec_pct)),
            "reception_perf_pct": to_float(cell_val(cells, i_rec_perf)),
            "profile_url": p_url,
            "photo_url": "",
        })

    if records:
        print(f"[DEBUG][CVF] Sample: {records[0]['name']} ({records[0]['team']}) pts={records[0]['points']} aces={records[0]['aces']} blk={records[0]['blocks']}")
    return records


def merge_cvf_records(merged_map, new_records):
    num_fields = [
        "matches", "sets", "points", "serve_total", "aces", "blocks",
        "attack_total", "attack_points", "attack_pct",
        "reception_total", "reception_pos_pct", "reception_perf_pct",
        "height_cm", "birth_year", "age",
    ]
    for r in new_records:
        key = r["player_id"]
        if key not in merged_map:
            merged_map[key] = r
            continue
        cur = merged_map[key]
        for f in num_fields:
            if r.get(f, 0) > cur.get(f, 0):
                cur[f] = r[f]
        if cur["position"] == "Unknown" and r["position"] != "Unknown":
            cur["position"] = r["position"]
            cur["raw_position"] = r["raw_position"]
        if not cur["team"] and r["team"]:
            cur["team"] = r["team"]
        if "/hrac" in r.get("profile_url", "").lower():
            cur["profile_url"] = r["profile_url"]

        sets = cur["sets"]
        cur["points_per_set"] = round(cur["points"] / sets, 2) if sets > 0 else 0.0
        cur["aces_per_set"] = round(cur["aces"] / sets, 2) if sets > 0 else 0.0
        cur["blocks_per_set"] = round(cur["blocks"] / sets, 2) if sets > 0 else 0.0


def enrich_cvf_bio(session, rec):
    p_url = rec.get("profile_url", "")
    if not p_url or "/hrac" not in p_url.lower():
        return rec
    try:
        resp = session.get(p_url, timeout=20)
        if resp.status_code != 200:
            return rec
        soup = BeautifulSoup(resp.text, "lxml")
        lines = [clean_text(x) for x in soup.get_text("\n").splitlines() if clean_text(x)]
        for i, line in enumerate(lines[:-1]):
            low = line.rstrip(":").lower()
            nxt = lines[i + 1]
            if low in ("post", "position") and rec["position"] == "Unknown":
                rec["raw_position"] = nxt
                rec["position"] = normalize_cz_position(nxt)
            elif low in ("výška", "vyska", "height") and rec["height_cm"] == 0:
                h_val = to_int(nxt)
                if 140 <= h_val <= 235:
                    rec["height_cm"] = h_val
            elif low in ("datum narození", "ročník") and rec["birth_year"] == 0:
                ym = re.search(r"(19\d{2}|20[0-1]\d)", nxt)
                if ym:
                    rec["birth_year"] = int(ym.group(1))
                    rec["age"] = datetime.now().year - rec["birth_year"]
    except Exception:
        pass
    return rec


def scrape_cvf_cz_league(browser, session, league):
    entry_url = league["url"].rstrip("/") + "/"
    merged_map = {}
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=HEADERS["User-Agent"])
    page = context.new_page()

    try:
        urls_to_visit = [entry_url]
        visited = set()

        while urls_to_visit and len(visited) < 12:
            s_url = urls_to_visit.pop(0)
            norm_u = s_url.rstrip("/")
            if norm_u in visited:
                continue
            visited.add(norm_u)

            print(f"[{league['name']}] Opening {s_url}")
            try:
                resp = page.goto(s_url, wait_until="networkidle", timeout=40000)
                if resp and resp.status >= 400:
                    continue
            except Exception as exc:
                print(f"[WARN] CVF navigation error on {s_url}: {exc}")
                continue

            page.wait_for_timeout(1200)
            s_soup = BeautifulSoup(page.content(), "lxml")
            tables = s_soup.find_all("table")
            print(f"[DEBUG][CVF] Found {len(tables)} tables on {s_url}")

            if norm_u == entry_url.rstrip("/"):
                for a in s_soup.find_all("a", href=True):
                    href = urljoin(s_url, a["href"])
                    h_norm = href.rstrip("/")
                    if (
                        "/zebricky/2026-27/ex-m/" in href.lower()
                        and not h_norm.lower().endswith("/ex-m/ex-m")
                        and h_norm not in visited
                        and href not in urls_to_visit
                    ):
                        urls_to_visit.append(href)

            for table in tables:
                parsed = parse_cvf_table(table, league, s_url)
                if parsed:
                    merge_cvf_records(merged_map, parsed)

        records = list(merged_map.values())
        print(f"[{league['name']}] Total merged Czech players: {len(records)}")

        if records:
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(enrich_cvf_bio, session, r) for r in records]
                records = [fut.result() for fut in as_completed(futures)]
    except Exception as exc:
        print(f"[WARN] CVF error: {exc}")
    finally:
        context.close()

    return records
