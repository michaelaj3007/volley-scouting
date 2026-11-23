import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urljoin, urlparse

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

JS_EXTRACT_SECTIONS = """() => {
    const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const surnames = Array.from(document.querySelectorAll('.list__item__surname'));
    const listMap = new Map();

    for (const sn of surnames) {
        const item = sn.closest('.list__item, li, a') || sn.parentElement?.parentElement?.parentElement;
        if (!item) continue;
        const listParent = item.parentElement;
        if (!listParent) continue;
        if (!listMap.has(listParent)) listMap.set(listParent, new Set());
        listMap.get(listParent).add(item);
    }

    const pageHead = Array.from(document.querySelectorAll('h1, h2'))
        .filter(h => !h.closest('.list__item'))
        .map(h => clean(h.innerText))
        .filter(Boolean)
        .slice(0, 3)
        .join(' | ');

    const sections = [];

    for (const [listParent, itemSet] of listMap.entries()) {
        // Walk up to find the card/column wrapper holding this list's title & DALŠÍ link
        let wrapper = listParent;
        let titleParts = [];
        let dalsiHref = '';

        for (let up = 0; up < 4 && wrapper; up++) {
            const heads = Array.from(wrapper.querySelectorAll('h1, h2, h3, h4, [class*="head"], [class*="title"]'))
                .filter(el => !el.closest('.list__item') && !listParent.contains(el))
                .map(el => clean(el.innerText))
                .filter(t => t && t.length < 90 && !/^další$/i.test(t));

            const dalsiLinks = Array.from(wrapper.querySelectorAll('a[href]'))
                .filter(a => !a.closest('.list__item') && /další|zobrazit|vše/i.test(clean(a.innerText)));

            if (dalsiLinks.length === 1 && !dalsiHref) {
                dalsiHref = dalsiLinks[0].href || '';
            }
            if (heads.length > 0 && titleParts.length === 0) {
                titleParts = Array.from(new Set(heads));
            }
            if (titleParts.length > 0 && dalsiHref) break;
            if (wrapper.parentElement && wrapper.parentElement.querySelectorAll('.list__item__surname').length > itemSet.size * 2) {
                break;
            }
            wrapper = wrapper.parentElement;
        }

        const title = titleParts.length > 0 ? titleParts.join(' | ') : pageHead;
        const items = [];

        for (const item of itemSet) {
            const mainInfo = item.querySelector('.list__item__main__info') || item;
            const snEl = item.querySelector('.list__item__surname');
            const nameBox = item.querySelector('.list__item__highlighted, h3') || mainInfo;

            const surname = clean(snEl ? snEl.innerText : '');
            const fullName = clean(nameBox ? nameBox.innerText : surname);

            // Profile URL
            let href = '';
            if (item.tagName === 'A' && item.href) {
                href = item.href;
            } else {
                const a = item.querySelector('a[href]');
                if (a) href = a.href;
            }

            // Images: team logo (inside mainInfo) & player photo (outside mainInfo)
            let team = '';
            const mainImgs = Array.from(mainInfo.querySelectorAll('img'));
            for (const img of mainImgs) {
                const label = clean(img.alt || img.title || '');
                if (label && !label.toLowerCase().includes(surname.toLowerCase())) {
                    team = label;
                    break;
                }
            }
            if (!team) {
                const teamEl = mainInfo.querySelector('[class*="team"], [class*="club"]');
                if (teamEl) team = clean(teamEl.innerText);
            }

            let photo = '';
            for (const img of Array.from(item.querySelectorAll('img'))) {
                if (mainInfo.contains(img) && img !== item.querySelector('img')) continue;
                const src = img.src || '';
                if (src && !/no[-_]?photo|placeholder|default|logo|club|team/i.test(src)) {
                    photo = src;
                    break;
                }
            }

            // Collect ONLY stat text (exclude mainInfo and rank numbers like "1.")
