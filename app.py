import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from crm_manager import load_crm_data, load_logo_assets, save_crm_data
from i18n import (
    AGENT_OPTIONS,
    CONTACT_STATUSES,
    POS_OPTIONS,
    label_agent,
    label_contact,
    t,
)

st.set_page_config(page_title="MSA Volley Scouting", page_icon="🏐", layout="wide")

DATA_PATH = "data/players.csv"
META_PATH = "data/last_updated.json"
OVERRIDES_PATH = "data/manual_overrides.csv"
EMPTY_PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="

st.session_state.setdefault("lang", "el")
logo_path, logo_thumb = load_logo_assets()


def lang_selector(key_name):
    opts = ["el", "en"]
    cur = st.session_state.get("lang", "el")
    picked = st.selectbox(
        "Language",
        opts,
        index=opts.index(cur),
        format_func=lambda c: "🇬🇷 Ελληνικά" if c == "el" else "🇬🇧 English",
        label_visibility="collapsed",
        key=key_name,
    )
    if picked != cur:
        st.session_state["lang"] = picked
        st.rerun()


def check_password():
    try:
        app_pwd = str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        app_pwd = ""
    if not app_pwd:
        return False
    if st.session_state.get("authenticated", False):
        return True

    lang = st.session_state["lang"]
    st.markdown("<br>", unsafe_allow_html=True)
    _, center, _ = st.columns([1, 1.4, 1])
    with center:
        lang_selector("login_lang_sel")
        if logo_path:
            st.image(logo_path, width=140)
        st.title("MSA Volley Scouting")
        st.caption(t(lang, "login_caption"))
        with st.form("login_form"):
            entered = st.text_input(t(lang, "login_pwd"), type="password", placeholder="••••••••")
            if st.form_submit_button(t(lang, "login_btn"), width="stretch"):
                if entered == app_pwd:
                    st.session_state["authenticated"] = True
                    st.rerun()
                st.error(t(lang, "login_err"))
    st.stop()


is_protected = check_password()
lang = st.session_state["lang"]

if "crm_data" not in st.session_state:
    crm_loaded, crm_status = load_crm_data()
    st.session_state["crm_data"] = crm_loaded
    st.session_state["crm_status"] = crm_status


def persist_crm(success_msg=None):
    ok, msg = save_crm_data(st.session_state["crm_data"])
    if ok:
        st.session_state["crm_status"] = "connected"
        st.session_state["flash_msg"] = ("success", success_msg or t(lang, "saved_ok"))
    else:
        err_txt = t(lang, "err_no_token") if "GITHUB_TOKEN" in msg else msg
        st.session_state["flash_msg"] = ("warning", err_txt)


@st.cache_data(ttl=300)
def load_base_players():
    if not os.path.exists(DATA_PATH):
        return pd.DataFrame(), {}
    df = pd.read_csv(DATA_PATH)
    meta = {}
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)

    num_cols = [
        "height_cm", "birth_year", "age", "matches", "sets", "points", "points_per_set",
        "break_points", "won_lost", "serve_total", "aces", "aces_per_set", "serve_errors",
        "blocks", "blocks_per_set", "attack_total", "attack_points", "attack_errors",
        "attack_blocked", "attack_pct", "attack_eff_pct", "reception_total",
        "reception_pos_pct", "reception_perf_pct",
    ]
    for col in num_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    for col in [
        "player_id", "name", "team", "league", "country", "gender",
        "position", "nationality", "profile_url", "photo_url",
    ]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    if os.path.exists(OVERRIDES_PATH):
        try:
            ov = pd.read_csv(OVERRIDES_PATH).set_index("player_id").to_dict(orient="index")
            for idx, r in df.iterrows():
                entry = ov.get(r["player_id"])
                if entry:
                    if pd.notna(entry.get("height_cm")) and int(float(entry["height_cm"])) > 0:
                        df.at[idx, "height_cm"] = int(float(entry["height_cm"]))
                    if pd.notna(entry.get("position")) and str(entry["position"]).strip():
                        df.at[idx, "position"] = str(entry["position"]).strip()
        except Exception:
            pass
    return df, meta


def merge_crm(base_df, crm_data, cur_lang):
    df = base_df.copy()
    p_map = crm_data.get("players", {})
    df["is_our_client"] = False
    df["contact_status"] = CONTACT_STATUSES[0]
    df["phone"] = ""
    df["email"] = ""
    df["social"] = ""
    df["has_agent"] = AGENT_OPTIONS[0]
    df["agent_name"] = ""
    df["contract_until"] = ""
    df["notes"] = ""
    df["shortlists_list"] = [[] for _ in range(len(df))]
    df["shortlists_str"] = ""

    for idx, row in df.iterrows():
        info = p_map.get(row["player_id"])
        if not info:
            continue
        df.at[idx, "is_our_client"] = bool(info.get("is_our_client", False))
        df.at[idx, "contact_status"] = info.get("contact_status") or CONTACT_STATUSES[0]
        df.at[idx, "phone"] = info.get("phone", "")
        df.at[idx, "email"] = info.get("email", "")
        df.at[idx, "social"] = info.get("social", "")
        df.at[idx, "has_agent"] = info.get("has_agent") or AGENT_OPTIONS[0]
        df.at[idx, "agent_name"] = info.get("agent_name", "")
        df.at[idx, "contract_until"] = info.get("contract_until", "")
        df.at[idx, "notes"] = info.get("notes", "")
        sls = info.get("shortlists") or []
        df.at[idx, "shortlists_list"] = sls
        df.at[idx, "shortlists_str"] = ", ".join(sls)
        h_ov = int(info.get("height_cm", 0) or 0)
        if h_ov > 0:
            df.at[idx, "height_cm"] = h_ov
        pos_ov = str(info.get("position", "") or "").strip()
        if pos_ov:
            df.at[idx, "position"] = pos_ov

    df["contact_display"] = df["contact_status"].map(lambda v: label_contact(v, cur_lang))
    df["agent_display"] = df["has_agent"].map(lambda v: label_agent(v, cur_lang))
    df["msa_badge"] = df["is_our_client"].map(
        lambda x: (logo_thumb if logo_thumb else "⭐") if x else (EMPTY_PIXEL if logo_thumb else "")
    )
    return df


def render_styled_table(source_df, cols, col_config, height=None, rank_index=False):
    view = source_df.copy().reset_index(drop=True)
    if rank_index:
        view.index = view.index + 1
    client_flags = view["is_our_client"].copy()

    use_cols = [c for c in ["msa_badge"] + cols if c in view.columns]
    out = view[use_cols].copy()
    if "height_cm" in out.columns:
        out["height_cm"] = out["height_cm"].replace(0, pd.NA)
    if "age" in out.columns:
        out["age"] = out["age"].replace(0, pd.NA)

    cfg = dict(col_config)
    cfg["msa_badge"] = (
        st.column_config.ImageColumn("MSA", width="small")
        if logo_thumb
        else st.column_config.TextColumn("MSA", width="small")
    )

    def highlight_clients(row):
        if client_flags.loc[row.name]:
            return ["color: #F5B041; font-weight: 700; background-color: rgba(245, 176, 65, 0.10)"] * len(row)
        return [""] * len(row)

    styled = out.style.apply(highlight_clients, axis=1)
    kwargs = {"width": "stretch", "column_config": cfg, "hide_index": not rank_index}
    if height:
        kwargs["height"] = height
    st.dataframe(styled, **kwargs)


base_df, meta = load_base_players()
crm = st.session_state["crm_data"]
df = merge_crm(base_df, crm, lang) if not base_df.empty else base_df

# -------------------- HEADER --------------------
h_cols = st.columns([0.8, 3.8, 1.3, 1.0]) if logo_path else st.columns([4.6, 1.3, 1.0])
if logo_path:
    with h_cols[0]:
        st.image(logo_path, width=78)
    title_col, lang_col, btn_col = h_cols[1], h_cols[2], h_cols[3]
else:
    title_col, lang_col, btn_col = h_cols[0], h_cols[1], h_cols[2]

with title_col:
    st.title("MSA Volley Scouting")
with lang_col:
    lang_selector("header_lang_sel")
with btn_col:
    c_ref, c_out = st.columns(2)
    if c_ref.button("🔄", help=t(lang, "btn_refresh_help"), width="stretch"):
        st.session_state["crm_data"], st.session_state["crm_status"] = load_crm_data()
        st.rerun()
    if is_protected and c_out.button("🔒", help=t(lang, "btn_logout_help"), width="stretch"):
        st.session_state["authenticated"] = False
        st.rerun()

if "flash_msg" in st.session_state:
    kind, txt = st.session_state.pop("flash_msg")
    getattr(st, kind)(txt)

if st.session_state.get("crm_status") == "no_token":
    st.warning(t(lang, "warn_no_token"))

if df.empty:
    st.warning(t(lang, "warn_no_data"))
    st.stop()

last_upd = meta.get("last_updated_utc", "—")
our_count = int(df["is_our_client"].sum())
st.caption(t(lang, "caption_meta", last_upd=last_upd, total=len(df), ours=our_count))

# -------------------- SIDEBAR --------------------
if logo_path:
    st.sidebar.image(logo_path, width=140)
st.sidebar.header(t(lang, "sidebar_title"))

search_q = st.sidebar.text_input(t(lang, "search_lbl"), placeholder=t(lang, "search_ph"))
only_clients = st.sidebar.checkbox(t(lang, "only_clients"), value=False)

all_shortlists = crm.get("shortlists", [])
sel_shortlist = st.sidebar.selectbox(
    t(lang, "filter_sl"),
    [""] + all_shortlists,
    format_func=lambda x: t(lang, "all_opt") if x == "" else x,
)
sel_contact = st.sidebar.multiselect(
    t(lang, "filter_contact"),
    CONTACT_STATUSES,
    format_func=lambda x: label_contact(x, lang),
)

leagues_opt = sorted([x for x in df["league"].unique() if x])
sel_leagues = st.sidebar.multiselect(t(lang, "filter_league"), leagues_opt, default=leagues_opt)

positions_opt = sorted([x for x in df["position"].unique() if x])
sel_positions = st.sidebar.multiselect(t(lang, "filter_pos"), positions_opt, default=positions_opt)

teams_opt = sorted([x for x in df["team"].unique() if x])
sel_teams = st.sidebar.multiselect(t(lang, "filter_team"), teams_opt)

nations_opt = sorted([x for x in df["nationality"].unique() if x])
sel_nations = st.sidebar.multiselect(t(lang, "filter_nat"), nations_opt)

st.sidebar.markdown("---")
min_height = st.sidebar.slider(t(lang, "min_height"), 165, 220, 165)
inc_unk_h = st.sidebar.checkbox(t(lang, "inc_unk_h"), value=(min_height == 165))
age_range = st.sidebar.slider(t(lang, "age_range"), 15, 45, (15, 45))
inc_unk_age = st.sidebar.checkbox(t(lang, "inc_unk_age"), value=True)
min_sets = st.sidebar.number_input(t(lang, "min_sets"), 0, 200, 0)

# Apply filters
fdf = df.copy()
if only_clients:
    fdf = fdf[fdf["is_our_client"]]
if sel_shortlist:
    fdf = fdf[fdf["shortlists_list"].apply(lambda lst: sel_shortlist in lst)]
if sel_contact:
    fdf = fdf[fdf["contact_status"].isin(sel_contact)]
if sel_leagues:
    fdf = fdf[fdf["league"].isin(sel_leagues)]
if sel_positions:
    fdf = fdf[fdf["position"].isin(sel_positions)]
if sel_teams:
    fdf = fdf[fdf["team"].isin(sel_teams)]
if sel_nations:
    fdf = fdf[fdf["nationality"].isin(sel_nations)]

if search_q.strip():
    q = search_q.strip().lower()
    fdf = fdf[
        fdf["name"].str.lower().str.contains(q, na=False)
        | fdf["team"].str.lower().str.contains(q, na=False)
        | fdf["agent_name"].str.lower().str.contains(q, na=False)
        | fdf["notes"].str.lower().str.contains(q, na=False)
    ]

fdf = fdf[(fdf["height_cm"] >= min_height) | (fdf["height_cm"] == 0)] if inc_unk_h else fdf[fdf["height_cm"] >= min_height]
fdf = fdf[((fdf["age"] >= age_range[0]) & (fdf["age"] <= age_range[1])) | (fdf["age"] == 0)] if inc_unk_age else fdf[(fdf["age"] >= age_range[0]) & (fdf["age"] <= age_range[1])]
fdf = fdf[fdf["sets"] >= min_sets]

k1, k2, k3, k4 = st.columns(4)
k1.metric(t(lang, "kpi_players"), len(fdf))
k2.metric(t(lang, "kpi_ours"), int(fdf["is_our_client"].sum()))
k3.metric(t(lang, "kpi_scorer"), f"{int(fdf['points'].max())} {t(lang, 'pts_unit')}" if not fdf.empty else "—")
k4.metric(t(lang, "kpi_blocker"), f"{int(fdf['blocks'].max())} {t(lang, 'blk_unit')}" if not fdf.empty else "—")

COMMON_CFG = {
    "name": st.column_config.TextColumn(t(lang, "col_name"), width="medium"),
    "team": st.column_config.TextColumn(t(lang, "col_team"), width="medium"),
    "league": t(lang, "col_league"),
    "position": st.column_config.TextColumn(t(lang, "col_pos"), width="small"),
    "height_cm": st.column_config.NumberColumn(t(lang, "col_height"), format="%d cm", width="small"),
    "age": st.column_config.NumberColumn(t(lang, "col_age"), format="%d", width="small"),
    "nationality": t(lang, "col_nat"),
    "matches": t(lang, "col_matches"),
    "sets": st.column_config.NumberColumn(t(lang, "col_sets"), format="%d", width="small"),
    "points": st.column_config.NumberColumn(t(lang, "col_points"), format="%d", width="small"),
    "points_per_set": st.column_config.NumberColumn(t(lang, "col_pts_set"), format="%.2f", width="small"),
    "aces": st.column_config.NumberColumn(t(lang, "col_aces"), format="%d", width="small"),
    "aces_per_set": st.column_config.NumberColumn(t(lang, "col_aces_set"), format="%.2f", width="small"),
    "blocks": st.column_config.NumberColumn(t(lang, "col_blocks"), format="%d", width="small"),
    "blocks_per_set": st.column_config.NumberColumn(t(lang, "col_blk_set"), format="%.2f", width="small"),
    "attack_points": st.column_config.NumberColumn(t(lang, "col_atk_pts"), format="%d", width="small"),
    "attack_pct": st.column_config.NumberColumn(t(lang, "col_atk_pct"), format="%.1f%%", width="small"),
    "attack_eff_pct": st.column_config.NumberColumn(t(lang, "col_atk_eff"), format="%.1f%%", width="small"),
    "reception_total": st.column_config.NumberColumn(t(lang, "col_rec_tot"), format="%d", width="small"),
    "reception_pos_pct": st.column_config.NumberColumn(t(lang, "col_rec_pos"), format="%.1f%%", width="small"),
    "reception_perf_pct": st.column_config.NumberColumn(t(lang, "col_rec_perf"), format="%.1f%%", width="small"),
    "contact_display": t(lang, "col_contact"),
    "agent_display": t(lang, "col_has_agent"),
    "agent_name": t(lang, "col_agent"),
    "contract_until": t(lang, "col_contract"),
    "shortlists_str": t(lang, "col_shortlists"),
    "notes": t(lang, "col_notes"),
    "phone": t(lang, "col_phone"),
    "email": t(lang, "col_email"),
    "social": t(lang, "col_social"),
    "profile_url": st.column_config.LinkColumn(t(lang, "col_profile"), display_text=t(lang, "col_open")),
}

# -------------------- TABS --------------------
tab_leaders, tab_db, tab_card, tab_crm = st.tabs([
    t(lang, "tab_leaders"),
    t(lang, "tab_db"),
    t(lang, "tab_card"),
    t(lang, "tab_crm"),
])

with tab_leaders:
    c1, c2, c3 = st.columns(3)
    top_n = c1.slider(t(lang, "lb_top_n"), 5, 30, 10)
    lb_min_sets = c2.number_input(t(lang, "lb_min_sets"), 1, 100, 3)
    rank_mode = c3.radio(
        t(lang, "lb_sort"),
        ["per_set", "total"],
        format_func=lambda x: t(lang, "rank_per_set") if x == "per_set" else t(lang, "rank_total"),
        horizontal=True,
    )
    ldf = fdf[fdf["sets"] >= lb_min_sets]

    if ldf.empty:
        st.info(t(lang, "no_players_criteria"))
    else:
        def show_board(title, data, sort_col, stat_cols):
            st.markdown(f"### {title}")
            cols = ["name"] + stat_cols + ["team", "position", "height_cm", "age", "sets"]
            top_df = data.sort_values(by=sort_col, ascending=False).head(top_n)
            render_styled_table(top_df, cols, COMMON_CFG, rank_index=True)

        ca, cb = st.columns(2)
        with ca:
            if rank_mode == "per_set":
                show_board(t(lang, "board_scorer"), ldf, "points_per_set", ["points_per_set", "points"])
                show_board(t(lang, "board_blocker"), ldf, "blocks_per_set", ["blocks_per_set", "blocks"])
            else:
                show_board(t(lang, "board_scorer"), ldf, "points", ["points", "points_per_set"])
                show_board(t(lang, "board_blocker"), ldf, "blocks", ["blocks", "blocks_per_set"])
        with cb:
            if rank_mode == "per_set":
                show_board(t(lang, "board_server"), ldf, "aces_per_set", ["aces_per_set", "aces"])
            else:
                show_board(t(lang, "board_server"), ldf, "aces", ["aces", "aces_per_set"])
            atk = ldf[ldf["attack_total"] >= 15]
            show_board(t(lang, "board_attacker"), atk if not atk.empty else ldf[ldf["attack_total"] > 0], "attack_pct", ["attack_pct", "attack_eff_pct", "attack_points"])

        rec = ldf[ldf["reception_total"] >= 15]
        if not rec.empty:
            show_board(t(lang, "board_receiver"), rec, "reception_pos_pct", ["reception_pos_pct", "reception_perf_pct", "reception_total"])

with tab_db:
    with st.expander(t(lang, "quick_add_exp"), expanded=False):
        if not all_shortlists:
            st.info(t(lang, "create_sl_first"))
        elif not fdf.empty:
            qc1, qc2, qc3 = st.columns([3, 2, 1.2])
            s_fdf = fdf.sort_values("name").reset_index(drop=True)
            lbl_to_pid = {f"{r['name']} — {r['team']} ({r['league']})": r["player_id"] for _, r in s_fdf.iterrows()}
            picked_lbls = qc1.multiselect(t(lang, "pick_players"), list(lbl_to_pid.keys()), key="quick_pick")
            target_sl = qc2.selectbox(t(lang, "which_sl"), all_shortlists, key="quick_sl")
            if qc3.button(t(lang, "btn_add"), type="primary", width="stretch", key="quick_add_btn"):
                if picked_lbls:
                    for lbl in picked_lbls:
                        pid = lbl_to_pid[lbl]
                        entry = crm["players"].setdefault(pid, {})
                        sls = list(entry.get("shortlists") or [])
                        if target_sl not in sls:
                            sls.append(target_sl)
                        entry["shortlists"] = sls
                    persist_crm(t(lang, "msg_added_sl", n=len(picked_lbls), sl=target_sl))
                    st.rerun()

    db_cols = [
        "name", "team", "league", "position", "height_cm", "age", "nationality",
        "sets", "points", "points_per_set", "aces", "blocks", "attack_pct", "reception_pos_pct",
        "contact_display", "agent_name", "shortlists_str", "notes", "profile_url",
    ]
    if fdf.empty:
        st.info(t(lang, "no_players_found"))
    else:
        render_styled_table(fdf, db_cols, COMMON_CFG, height=580)
        csv_data = fdf[db_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button(t(lang, "btn_download_csv"), data=csv_data, file_name="msa_scouting.csv", mime="text/csv")

with tab_card:
    if fdf.empty:
        st.info(t(lang, "no_players_filters"))
    else:
        sorted_fdf = fdf.sort_values("name").reset_index(drop=True)
        options = [f"{'⭐ ' if r['is_our_client'] else ''}{r['name']} — {r['team']}" for _, r in sorted_fdf.iterrows()]
        chosen = st.selectbox(t(lang, "pick_player"), options, key="card_select")
        p = sorted_fdf.iloc[options.index(chosen)]
        pid = p["player_id"]

        c_left, c_right = st.columns([1.2, 2.8])
        with c_left:
            if p["photo_url"] and "noimage" not in p["photo_url"].lower():
                st.image(p["photo_url"], width=170)
            if p["is_our_client"]:
                st.success(t(lang, "badge_our_client"))
            st.markdown(f"**{t(lang, 'lbl_team')}:** {p['team']}")
            st.markdown(f"**{t(lang, 'lbl_league')}:** {p['league']}")
            st.markdown(f"**{t(lang, 'lbl_pos')}:** {p['position']}")
            st.markdown(f"**{t(lang, 'lbl_height')}:** {int(p['height_cm'])} cm" if p["height_cm"] > 0 else f"**{t(lang, 'lbl_height')}:** —")
            st.markdown(f"**{t(lang, 'lbl_age')}:** {int(p['age'])} ({int(p['birth_year'])})" if p["age"] > 0 else f"**{t(lang, 'lbl_age')}:** —")
            st.markdown(f"**{t(lang, 'lbl_nat')}:** {p['nationality']}")
            if p["profile_url"]:
                st.link_button(t(lang, "btn_official_profile"), p["profile_url"])

        with c_right:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(t(lang, "m_matches_sets"), f"{int(p['matches'])} / {int(p['sets'])}")
            m2.metric(t(lang, "m_points"), f"{int(p['points'])} ({p['points_per_set']:.2f})")
            m3.metric(t(lang, "m_aces"), f"{int(p['aces'])} ({p['aces_per_set']:.2f})")
            m4.metric(t(lang, "m_blocks"), f"{int(p['blocks'])} ({p['blocks_per_set']:.2f})")

            m5, m6, m7, m8 = st.columns(4)
            m5.metric(t(lang, "m_atk_pct"), f"{p['attack_pct']:.1f}%")
            m6.metric(t(lang, "m_atk_eff"), f"{p['attack_eff_pct']:.1f}%")
            m7.metric(t(lang, "m_rec_pos"), f"{p['reception_pos_pct']:.1f}%")
            m8.metric(t(lang, "m_rec_perf"), f"{p['reception_perf_pct']:.1f}%")

            st.markdown("---")
            st.subheader(t(lang, "crm_header"))
            existing = crm["players"].get(pid, {})
            cur_sls = [s for s in (existing.get("shortlists") or []) if s in all_shortlists]

            with st.form(f"crm_form_{pid}"):
                f1, f2 = st.columns(2)
                is_client_val = f1.checkbox(t(lang, "crm_is_client"), value=bool(existing.get("is_our_client", False)))
                c_stat_idx = CONTACT_STATUSES.index(existing["contact_status"]) if existing.get("contact_status") in CONTACT_STATUSES else 0
                contact_val = f2.selectbox(
                    t(lang, "crm_contact_status"),
                    CONTACT_STATUSES,
                    index=c_stat_idx,
                    format_func=lambda x: label_contact(x, lang),
                )

                s1, s2 = st.columns([2, 1.2])
                sel_sls = s1.multiselect(t(lang, "crm_add_sl"), all_shortlists, default=cur_sls)
                new_sl_inline = s2.text_input(t(lang, "crm_new_sl"), placeholder=t(lang, "crm_new_sl_ph"))

                ct1, ct2, ct3 = st.columns(3)
                phone_val = ct1.text_input(t(lang, "crm_phone"), value=existing.get("phone", ""))
                email_val = ct2.text_input(t(lang, "crm_email"), value=existing.get("email", ""))
                social_val = ct3.text_input(t(lang, "crm_social"), value=existing.get("social", ""))

                ag1, ag2, ag3 = st.columns(3)
                ag_idx = AGENT_OPTIONS.index(existing["has_agent"]) if existing.get("has_agent") in AGENT_OPTIONS else 0
                has_ag_val = ag1.selectbox(
                    t(lang, "crm_has_agent"),
                    AGENT_OPTIONS,
                    index=ag_idx,
                    format_func=lambda x: label_agent(x, lang),
                )
                ag_name_val = ag2.text_input(t(lang, "crm_agent_name"), value=existing.get("agent_name", ""))
                contract_val = ag3.text_input(t(lang, "crm_contract"), value=existing.get("contract_until", ""), placeholder=t(lang, "crm_contract_ph"))

                ov1, ov2 = st.columns(2)
                h_ov_val = ov1.number_input(t(lang, "crm_height_ov"), 0, 235, int(existing.get("height_cm", 0) or 0))
                pos_cur = existing.get("position", "")
                pos_choices = [""] + POS_OPTIONS
                pos_ov_val = ov2.selectbox(
                    t(lang, "crm_pos_ov"),
                    pos_choices,
                    index=pos_choices.index(pos_cur) if pos_cur in POS_OPTIONS else 0,
                    format_func=lambda x: t(lang, "auto_opt") if x == "" else x,
                )

                notes_val = st.text_area(t(lang, "crm_notes"), value=existing.get("notes", ""), height=90)

                if st.form_submit_button(t(lang, "btn_save_crm"), type="primary", width="stretch"):
                    final_sls = list(sel_sls)
                    if new_sl_inline.strip():
                        ns = new_sl_inline.strip()
                        if ns not in crm["shortlists"]:
                            crm["shortlists"].append(ns)
                        if ns not in final_sls:
                            final_sls.append(ns)

                    crm["players"][pid] = {
                        "name": p["name"],
                        "is_our_client": is_client_val,
                        "contact_status": contact_val,
                        "shortlists": final_sls,
                        "phone": phone_val.strip(),
                        "email": email_val.strip(),
                        "social": social_val.strip(),
                        "has_agent": has_ag_val,
                        "agent_name": ag_name_val.strip(),
                        "contract_until": contract_val.strip(),
                        "height_cm": int(h_ov_val),
                        "position": pos_ov_val,
                        "notes": notes_val.strip(),
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    persist_crm(t(lang, "msg_saved_player", name=p["name"]))
                    st.rerun()

with tab_crm:
    sub_sl, sub_ours, sub_pipe = st.tabs([
        t(lang, "sub_sl"),
        t(lang, "sub_ours"),
        t(lang, "sub_pipe"),
    ])

    with sub_sl:
        mc1, mc2, _ = st.columns([2.5, 1, 2])
        new_sl_name = mc1.text_input(t(lang, "new_sl_lbl"), placeholder=t(lang, "new_sl_ph"), label_visibility="collapsed")
        if mc2.button(t(lang, "btn_create"), width="stretch"):
            if new_sl_name.strip() and new_sl_name.strip() not in crm["shortlists"]:
                crm["shortlists"].append(new_sl_name.strip())
                persist_crm(t(lang, "msg_created_sl", sl=new_sl_name.strip()))
                st.rerun()

        if not crm["shortlists"]:
            st.info(t(lang, "no_sl_yet"))
        else:
            active_sl = st.selectbox(t(lang, "select_sl_view"), crm["shortlists"], key="view_sl_box")
            sl_df = df[df["shortlists_list"].apply(lambda lst: active_sl in lst)]

            head_sl1, head_sl2 = st.columns([4, 1.2])
            head_sl1.subheader(t(lang, "sl_count_hdr", sl=active_sl, n=len(sl_df)))
            if head_sl2.button(t(lang, "btn_del_sl"), key="del_sl_btn"):
                crm["shortlists"] = [s for s in crm["shortlists"] if s != active_sl]
                for entry in crm["players"].values():
                    entry["shortlists"] = [s for s in (entry.get("shortlists") or []) if s != active_sl]
                persist_crm(t(lang, "msg_deleted_sl", sl=active_sl))
                st.rerun()

            if sl_df.empty:
                st.info(t(lang, "sl_empty"))
            else:
                sl_cols = [
                    "name", "team", "league", "position", "height_cm", "age",
                    "sets", "points_per_set", "aces_per_set", "blocks_per_set", "attack_pct",
                    "contact_display", "agent_name", "contract_until", "notes", "profile_url",
                ]
                render_styled_table(sl_df, sl_cols, COMMON_CFG)

                rc1, rc2 = st.columns([3, 1.2])
                rem_map = {f"{r['name']} — {r['team']}": r["player_id"] for _, r in sl_df.iterrows()}
                rem_pick = rc1.selectbox(t(lang, "rem_from_sl"), list(rem_map.keys()), key="rem_sl_pick")
                if rc2.button(t(lang, "btn_remove"), width="stretch", key="rem_sl_btn"):
                    rpid = rem_map[rem_pick]
                    crm["players"][rpid]["shortlists"] = [s for s in crm["players"][rpid].get("shortlists", []) if s != active_sl]
                    persist_crm(t(lang, "msg_removed_sl"))
                    st.rerun()

                st.download_button(
                    t(lang, "btn_export_sl", sl=active_sl),
                    data=sl_df[sl_cols].to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"shortlist_{active_sl}.csv",
                    mime="text/csv",
                )

    with sub_ours:
        ours_df = df[df["is_our_client"]]
        if ours_df.empty:
            st.info(t(lang, "no_ours_yet"))
        else:
            ours_cols = [
                "name", "team", "league", "position", "height_cm", "age",
                "sets", "points", "points_per_set", "aces", "blocks", "attack_pct",
                "contract_until", "notes", "profile_url",
            ]
            render_styled_table(ours_df, ours_cols, COMMON_CFG)

    with sub_pipe:
        pipe_df = df[(df["contact_status"] != CONTACT_STATUSES[0]) | (df["agent_name"] != "") | (df["phone"] != "")]
        if pipe_df.empty:
            st.info(t(lang, "no_pipe_yet"))
        else:
            pipe_cols = [
                "name", "team", "league", "position", "contact_display",
                "phone", "email", "social", "agent_display", "agent_name", "contract_until", "shortlists_str", "notes",
            ]
            render_styled_table(pipe_df, pipe_cols, COMMON_CFG)
