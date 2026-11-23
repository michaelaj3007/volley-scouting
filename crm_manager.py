import base64
import io
import json
import os

import requests
import streamlit as st
from PIL import Image

CRM_FILE = "crm_data.json"
DEFAULT_SHORTLISTS = ["Στόχοι 2027", "Ενδιαφέροντες"]


def default_crm():
    return {"shortlists": list(DEFAULT_SHORTLISTS), "players": {}}


def get_github_cfg():
    try:
        token = str(st.secrets.get("GITHUB_TOKEN", "")).strip()
        repo = str(st.secrets.get("CRM_REPO", "michaelaj3007/msa-crm-private")).strip()
        return token, repo
    except Exception:
        return "", ""


@st.cache_data(ttl=3600)
def load_logo_assets():
    candidates = [
        "logo.png", "logo.jpg", "logo.jpeg", "logo.webp",
        "assets/logo.png", "assets/logo.jpg", "assets/logo.jpeg", "assets/logo.webp",
    ]
    logo_path = next((p for p in candidates if os.path.exists(p)), None)
    if not logo_path:
        return None, None

    thumb_uri = None
    try:
        with Image.open(logo_path) as img:
            img = img.convert("RGBA")
            img.thumbnail((48, 48))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            thumb_uri = f"data:image/png;base64,{b64}"
    except Exception:
        thumb_uri = None

    return logo_path, thumb_uri


def _gh_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def load_crm_data():
    token, repo = get_github_cfg()
    if not token or not repo:
        return default_crm(), "no_token"

    url = f"https://api.github.com/repos/{repo}/contents/{CRM_FILE}"
    try:
        resp = requests.get(url, headers=_gh_headers(token), timeout=15)
        if resp.status_code == 200:
            payload = resp.json()
            raw = base64.b64decode(payload["content"]).decode("utf-8")
            data = json.loads(raw)
            data.setdefault("shortlists", list(DEFAULT_SHORTLISTS))
            data.setdefault("players", {})
            return data, "connected"
        if resp.status_code == 404:
            return default_crm(), "connected"
        return default_crm(), f"error_{resp.status_code}"
    except Exception:
        return default_crm(), "error_network"


def save_crm_data(data):
    token, repo = get_github_cfg()
    if not token or not repo:
        return False, "Δεν έχει ρυθμιστεί ακόμα το GITHUB_TOKEN στα Secrets (αποθηκεύτηκε μόνο προσωρινά)."

    url = f"https://api.github.com/repos/{repo}/contents/{CRM_FILE}"
    headers = _gh_headers(token)

    try:
        sha = None
        get_resp = requests.get(url, headers=headers, timeout=15)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")

        content_b64 = base64.b64encode(
            json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("ascii")

        body = {"message": "Update MSA Scouting CRM", "content": content_b64}
        if sha:
            body["sha"] = sha

        put_resp = requests.put(url, headers=headers, json=body, timeout=20)
        if put_resp.status_code in (200, 201):
            return True, "Αποθηκεύτηκε μόνιμα!"
        return False, f"Σφάλμα GitHub ({put_resp.status_code}): έλεγξε το token και το όνομα του repo."
    except Exception as exc:
        return False, f"Σφάλμα σύνδεσης: {exc}"
