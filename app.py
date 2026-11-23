import json
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from crm_manager import load_crm_data, load_logo_assets, save_crm_data

st.set_page_config(page_title="MSA Volley Scouting", page_icon="🏐", layout="wide")

DATA_PATH = "data/players.csv"
META_PATH = "data/last_updated.json"
OVERRIDES_PATH = "data/manual_overrides.csv"

CONTACT_STATUSES = [
    "Δεν έγινε επικοινωνία",
    "Έγινε επικοινωνία",
    "Σε συζητήσεις",
    "Αναμονή απάντησης",
    "Υπέγραψε",
    "Δεν ενδιαφέρεται",
]
AGENT_OPTIONS = ["Άγνωστο", "Όχι (Ελεύθερος)", "Ναι"]
POS_OPTIONS = ["Setter", "Opposite", "Outside Hitter", "Middle Blocker", "Libero", "Unknown"]

logo_path, logo_thumb = load_logo_assets()


def check_password():
    try:
        app_pwd = str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        app_pwd = ""
    if not app_pwd:
        return False
    if st.session_state.get("authenticated", False):
        return True

    st.markdown("<br><br>", unsafe_allow_html=True)
    _, center, _ = st.columns([1, 1.4, 1])
    with center:
        if logo_path:
            st.image(logo_path, width=140)
        st.title("MSA Volley Scouting")
        st.caption("Περιορισμένη πρόσβαση — Εισάγετε τον κωδικό σας.")
        with st.form("login_form"):
            entered = st.text_input("Κωδικός πρόσβασης", type="password", placeholder="••••••••")
            if st.form_submit_button("Είσοδος", width="stretch"):
                if entered == app_pwd:
                    st.session_state["authenticated"] = True
                    st.rerun()
                st.error("Λάθος κωδικός.")
    st.stop()


is_protected = check_password()

if "crm_data" not in st.session_state:
    crm_loaded, crm_status = load_crm_data()
    st.session_state["crm_data"] = crm_loaded
    st.session_state["crm_status"] = crm_status


def persist_crm(success_msg=None):
    ok, msg = save_crm_data(st.session_state["crm_data"])
    if ok:
        st.session_state["crm_status"] = "connected"
        st.session_state["flash_msg"] = ("success", success_msg or msg)
    else:
        st.session_state["flash_msg"] = ("warning", msg)


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


def merge_crm(base_df, crm_data):
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

    df["msa_badge"] = df["is_our_client"].map(
        lambda x: (logo_thumb if logo_thumb else "⭐") if x else (None if logo_thumb else "")
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
    if logo_thumb:
        cfg["msa_badge"] = st.column_config.ImageColumn("MSA", width="small")
    else:
        cfg["msa_badge"] = st.column_config.TextColumn("MSA", width="small")

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
df = merge_crm(base_df, crm) if not base_df.empty else base_df

# -------------------- HEADER --------------------
h_cols = st.columns([0.8, 4.5, 1.2]) if logo_path else st.columns([5.3, 1.2])
if logo_path:
    with h_cols[0]:
        st.image(logo_path, width=78)
    title_col, btn_col = h_cols[1], h_cols[2]
else:
    title_col, btn_col = h_cols[0], h_cols[1]

with title_col:
    st.title("MSA Volley Scouting")
with btn_col:
    c_ref, c_out = st.columns(2)
    if c_ref.button("🔄", help="Ανανέωση CRM", width="stretch"):
        st.session_state["crm_data"], st.session_state["crm_status"] = load_crm_data()
        st.rerun()
    if is_protected and c_out.button("🔒", help="Έξοδος", width="stretch"):
        st.session_state["authenticated"] = False
        st.rerun()

if "flash_msg" in st.session_state:
    kind, txt = st.session_state.pop("flash_msg")
    getattr(st, kind)(txt)

if st.session_state.get("crm_status") == "no_token":
    st.warning("⚙️ Πρόσθεσε `GITHUB_TOKEN` και `CRM_REPO` στα Streamlit Secrets για να αποθηκεύονται μόνιμα οι Shortlists και το CRM.")

if df.empty:
    st.warning("Δεν υπάρχουν ακόμα δεδομένα. Τρέξε το Daily Volleyball Stats Update από το GitHub Actions.")
    st.stop()

last_upd = meta.get("last_updated_utc", "—")
our_count = int(df["is_our_client"].sum())
st.caption(f"Τελευταία ενημέρωση: **{last_upd}**  |  Βάση: **{len(df)}** αθλητές  |  Αθλητές μας (MSA): **{our_count}**")

# -------------------- SIDEBAR --------------------
if logo_path:
    st.sidebar.image(logo_path, width=140)
st.sidebar.header("🔍 Φίλτρα Scouting")

search_q = st.sidebar.text_input("Αναζήτηση (όνομα / ομάδα / ατζέντης)", placeholder="π.χ. Jovovic, Galati...")
only_clients = st.sidebar.checkbox("⭐ Μόνο αθλητές μας (MSA)", value=False)

all_shortlists = crm.get("shortlists", [])
sel_shortlist = st.sidebar.selectbox("📌 Φίλτρο Shortlist", ["Όλες"] + all_shortlists)
sel_contact = st.sidebar.multiselect("📞 Κατάσταση Επικοινωνίας", CONTACT_STATUSES)

leagues_opt = sorted([x for x in df["league"].unique() if x])
sel_leagues = st.sidebar.multiselect("Πρωτάθλημα", leagues_opt, default=leagues_opt)

positions_opt = sorted([x for x in df["position"].unique() if x])
sel_positions = st.sidebar.multiselect("Θέση", positions_opt, default=positions_opt)

teams_opt = sorted([x for x in df["team"].unique() if x])
sel_teams = st.sidebar.multiselect("Ομάδα (προαιρετικό)", teams_opt)

nations_opt = sorted([x for x in df["nationality"].unique() if x])
sel_nations = st.sidebar.multiselect("Εθνικότητα (προαιρετικό)", nations_opt)

st.sidebar.markdown("---")
min_height = st.sidebar.slider("Ελάχιστο Ύψος (cm)", 165, 220, 165)
inc_unk_h = st.sidebar.checkbox("Και χωρίς δηλωμένο ύψος", value=(min_height == 165))
age_range = st.sidebar.slider("Ηλικία", 15, 45, (15, 45))
inc_unk_age = st.sidebar.checkbox("Και χωρίς ημ/νία γέννησης", value=True)
min_sets = st.sidebar.number_input("Ελάχιστα Σετ", 0, 200, 0)

# Apply filters
fdf = df.copy()
if only_clients:
    fdf = fdf[fdf["is_our_client"]]
if sel_shortlist != "Όλες":
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
k1.metric("Αθλητές (με φίλτρα)", len(fdf))
k2.metric("Αθλητές μας (MSA)", int(fdf["is_our_client"].sum()))
k3.metric("Κορυφαίος Σκόρερ", f"{int(fdf['points'].max())} π." if not fdf.empty else "—")
k4.metric("Κορυφαίος Μπλοκέρ", f"{int(fdf['blocks'].max())} μπλ." if not fdf.empty else "—")

COMMON_CFG = {
    "name": st.column_config.TextColumn("Αθλητής", width="medium"),
    "team": st.column_config.TextColumn("Ομάδα", width="medium"),
    "league": "Πρωτάθλημα",
    "position": st.column_config.TextColumn("Θέση", width="small"),
    "height_cm": st.column_config.NumberColumn("Ύψος", format="%d cm", width="small"),
    "age": st.column_config.NumberColumn("Ηλικία", format="%d", width="small"),
    "nationality": "Εθν.",
    "matches": "Αγ.",
    "sets": st.column_config.NumberColumn("Σετ", format="%d", width="small"),
    "points": st.column_config.NumberColumn("Πόντοι", format="%d", width="small"),
    "points_per_set": st.column_config.NumberColumn("Π/Σετ", format="%.2f", width="small"),
    "aces": st.column_config.NumberColumn("Άσοι", format="%d", width="small"),
    "aces_per_set": st.column_config.NumberColumn("Άσοι/Σετ", format="%.2f", width="small"),
    "blocks": st.column_config.NumberColumn("Μπλοκ", format="%d", width="small"),
    "blocks_per_set": st.column_config.NumberColumn("Μπλ/Σετ", format="%.2f", width="small"),
    "attack_points": st.column_config.NumberColumn("Επιθ.Π", format="%d", width="small"),
    "attack_pct": st.column_config.NumberColumn("Επίθ.%", format="%.1f%%", width="small"),
    "attack_eff_pct": st.column_config.NumberColumn("Αποτ.%", format="%.1f%%", width="small"),
    "reception_total": st.column_config.NumberColumn("Υπ.", format="%d", width="small"),
    "reception_pos_pct": st.column_config.NumberColumn("Θετ.%", format="%.1f%%", width="small"),
    "reception_perf_pct": st.column_config.NumberColumn("Άρ.%", format="%.1f%%", width="small"),
    "contact_status": "Επικοινωνία",
    "agent_name": "Ατζέντης",
    "contract_until": "Συμβόλαιο",
    "shortlists_str": "Shortlists",
    "notes": "Σημειώσεις",
    "profile_url": st.column_config.LinkColumn("Προφίλ", display_text="Άνοιγμα ↗"),
}

# -------------------- TABS --------------------
tab_leaders, tab_db, tab_card, tab_crm = st.tabs([
    "🏆 Κατατάξεις",
    "📋 Βάση Αθλητών",
    "👤 Καρτέλα & CRM Αθλητή",
    "⭐ Shortlists & Αθλητές μας",
])

with tab_leaders:
    c1, c2, c3 = st.columns(3)
    top_n = c1.slider("Πλήθος παικτών ανά στήλη", 5, 30, 10)
    lb_min_sets = c2.number_input("Ελάχιστα σετ", 1, 100, 3)
    rank_mode = c3.radio("Ταξινόμηση", ["Ανά Σετ (μ.ό.)", "Σύνολο"], horizontal=True)
    ldf = fdf[fdf["sets"] >= lb_min_sets]

    if ldf.empty:
        st.info("Δεν βρέθηκαν αθλητές με αυτά τα κριτήρια.")
    else:
        def show_board(title, data, sort_col, stat_cols):
            st.markdown(f"### {title}")
            cols = ["name"] + stat_cols + ["team", "position", "height_cm", "sets"]
            top_df = data.sort_values(by=sort_col, ascending=False).head(top_n)
            render_styled_table(top_df, cols, COMMON_CFG, rank_index=True)

        ca, cb = st.columns(2)
        with ca:
            if rank_mode == "Ανά Σετ (μ.ό.)":
                show_board("🔥 Καλύτερος Σκόρερ", ldf, "points_per_set", ["points_per_set", "points"])
                show_board("🧱 Καλύτερος Μπλοκέρ", ldf, "blocks_per_set", ["blocks_per_set", "blocks"])
            else:
                show_board("🔥 Καλύτερος Σκόρερ", ldf, "points", ["points", "points_per_set"])
                show_board("🧱 Καλύτερος Μπλοκέρ", ldf, "blocks", ["blocks", "blocks_per_set"])
        with cb:
            if rank_mode == "Ανά Σετ (μ.ό.)":
                show_board("🎯 Καλύτερος Σέρβερ", ldf, "aces_per_set", ["aces_per_set", "aces"])
            else:
                show_board("🎯 Καλύτερος Σέρβερ", ldf, "aces", ["aces", "aces_per_set"])
            atk = ldf[ldf["attack_total"] >= 15]
            show_board("⚡ Καλύτερος Επιθετικός", atk if not atk.empty else ldf[ldf["attack_total"] > 0], "attack_pct", ["attack_pct", "attack_eff_pct", "attack_points"])

        rec = ldf[ldf["reception_total"] >= 15]
        if not rec.empty:
            show_board("🛡️ Καλύτερος Υποδοχέας", rec, "reception_pos_pct", ["reception_pos_pct", "reception_perf_pct", "reception_total"])

with tab_db:
    with st.expander("➕ Γρήγορη προσθήκη παικτών σε Shortlist", expanded=False):
        if not all_shortlists:
            st.info("Δημιούργησε πρώτα μια shortlist στην καρτέλα ⭐ Shortlists.")
        elif not fdf.empty:
            qc1, qc2, qc3 = st.columns([3, 2, 1.2])
            s_fdf = fdf.sort_values("name").reset_index(drop=True)
            lbl_to_pid = {f"{r['name']} — {r['team']} ({r['league']})": r["player_id"] for _, r in s_fdf.iterrows()}
            picked_lbls = qc1.multiselect("Διάλεξε αθλητές", list(lbl_to_pid.keys()), key="quick_pick")
            target_sl = qc2.selectbox("Σε ποια Shortlist;", all_shortlists, key="quick_sl")
            if qc3.button("Προσθήκη", type="primary", width="stretch", key="quick_add_btn"):
                if picked_lbls:
                    for lbl in picked_lbls:
                        pid = lbl_to_pid[lbl]
                        entry = crm["players"].setdefault(pid, {})
                        sls = list(entry.get("shortlists") or [])
                        if target_sl not in sls:
                            sls.append(target_sl)
                        entry["shortlists"] = sls
                    persist_crm(f"Προστέθηκαν {len(picked_lbls)} αθλητές στη λίστα «{target_sl}»!")
                    st.rerun()

    db_cols = [
        "name", "team", "league", "position", "height_cm", "age", "nationality",
        "sets", "points", "points_per_set", "aces", "blocks", "attack_pct", "reception_pos_pct",
        "contact_status", "agent_name", "shortlists_str", "notes", "profile_url",
    ]
    if fdf.empty:
        st.info("Δεν βρέθηκαν αθλητές.")
    else:
        render_styled_table(fdf, db_cols, COMMON_CFG, height=580)
        csv_data = fdf[db_cols].to_csv(index=False).encode("utf-8-sig")
        st.download_button("⬇️ Κατέβασμα σε Excel / CSV", data=csv_data, file_name="msa_scouting.csv", mime="text/csv")

with tab_card:
    if fdf.empty:
        st.info("Δεν υπάρχουν αθλητές με τα τρέχοντα φίλτρα.")
    else:
        sorted_fdf = fdf.sort_values("name").reset_index(drop=True)
        options = [f"{'⭐ ' if r['is_our_client'] else ''}{r['name']} — {r['team']}" for _, r in sorted_fdf.iterrows()]
        chosen = st.selectbox("Διάλεξε αθλητή", options, key="card_select")
        p = sorted_fdf.iloc[options.index(chosen)]
        pid = p["player_id"]

        c_left, c_right = st.columns([1.2, 2.8])
        with c_left:
            if p["photo_url"] and "noimage" not in p["photo_url"].lower():
                st.image(p["photo_url"], width=170)
            if p["is_our_client"]:
                st.success("⭐ Αθλητής της εταιρείας μας (MSA)")
            st.markdown(f"**Ομάδα:** {p['team']}")
            st.markdown(f"**Πρωτάθλημα:** {p['league']}")
            st.markdown(f"**Θέση:** {p['position']}")
            st.markdown(f"**Ύψος:** {int(p['height_cm'])} cm" if p["height_cm"] > 0 else "**Ύψος:** —")
            st.markdown(f"**Ηλικία:** {int(p['age'])} ({int(p['birth_year'])})" if p["age"] > 0 else "**Ηλικία:** —")
            st.markdown(f"**Εθνικότητα:** {p['nationality']}")
            if p["profile_url"]:
                st.link_button("Επίσημο Προφίλ ↗", p["profile_url"])

        with c_right:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Αγώνες / Σετ", f"{int(p['matches'])} / {int(p['sets'])}")
            m2.metric("Πόντοι (μ.ό.)", f"{int(p['points'])} ({p['points_per_set']:.2f})")
            m3.metric("Άσοι (μ.ό.)", f"{int(p['aces'])} ({p['aces_per_set']:.2f})")
            m4.metric("Μπλοκ (μ.ό.)", f"{int(p['blocks'])} ({p['blocks_per_set']:.2f})")

            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Επίθεση %", f"{p['attack_pct']:.1f}%")
            m6.metric("Αποτελεσμ. %", f"{p['attack_eff_pct']:.1f}%")
            m7.metric("Θετ. Υποδοχή %", f"{p['reception_pos_pct']:.1f}%")
            m8.metric("Άριστη Υπ. %", f"{p['reception_perf_pct']:.1f}%")

            st.markdown("---")
            st.subheader("📇 CRM & Shortlist Αθλητή")
            existing = crm["players"].get(pid, {})
            cur_sls = [s for s in (existing.get("shortlists") or []) if s in all_shortlists]

            with st.form(f"crm_form_{pid}"):
                f1, f2 = st.columns(2)
                is_client_val = f1.checkbox("⭐ Αθλητής της εταιρείας μας (MSA)", value=bool(existing.get("is_our_client", False)))
                c_stat_idx = CONTACT_STATUSES.index(existing["contact_status"]) if existing.get("contact_status") in CONTACT_STATUSES else 0
                contact_val = f2.selectbox("Κατάσταση Επικοινωνίας", CONTACT_STATUSES, index=c_stat_idx)

                s1, s2 = st.columns([2, 1.2])
                sel_sls = s1.multiselect("📌 Προσθήκη σε Shortlist(s)", all_shortlists, default=cur_sls)
                new_sl_inline = s2.text_input("Ή νέα Shortlist", placeholder="π.χ. Κεντρικοί 2027")

                ct1, ct2, ct3 = st.columns(3)
                phone_val = ct1.text_input("Τηλέφωνο / WhatsApp", value=existing.get("phone", ""))
                email_val = ct2.text_input("Email", value=existing.get("email", ""))
                social_val = ct3.text_input("Instagram / Social", value=existing.get("social", ""))

                ag1, ag2, ag3 = st.columns(3)
                ag_idx = AGENT_OPTIONS.index(existing["has_agent"]) if existing.get("has_agent") in AGENT_OPTIONS else 0
                has_ag_val = ag1.selectbox("Έχει ατζέντη;", AGENT_OPTIONS, index=ag_idx)
                ag_name_val = ag2.text_input("Ατζέντης / Γραφείο", value=existing.get("agent_name", ""))
                contract_val = ag3.text_input("Λήξη Συμβολαίου", value=existing.get("contract_until", ""), placeholder="π.χ. Μάιος 2027")

                ov1, ov2 = st.columns(2)
                h_ov_val = ov1.number_input("Διόρθωση Ύψους (cm, 0 = αυτόματο)", 0, 235, int(existing.get("height_cm", 0) or 0))
                pos_cur = existing.get("position", "")
                pos_choices = ["(Αυτόματη)"] + POS_OPTIONS
                pos_ov_val = ov2.selectbox("Διόρθωση Θέσης", pos_choices, index=pos_choices.index(pos_cur) if pos_cur in POS_OPTIONS else 0)

                notes_val = st.text_area("Σημειώσεις Scouting & Επικοινωνίας", value=existing.get("notes", ""), height=90)

                if st.form_submit_button("💾 Αποθήκευση Προφίλ & Shortlist", type="primary", width="stretch"):
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
                        "position": "" if pos_ov_val == "(Αυτόματη)" else pos_ov_val,
                        "notes": notes_val.strip(),
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    }
                    persist_crm(f"Αποθηκεύτηκε το προφίλ του {p['name']}!")
                    st.rerun()

with tab_crm:
    sub_sl, sub_ours, sub_pipe = st.tabs(["📌 Οι Shortlists μου", "⭐ Οι Αθλητές μας (MSA)", "📞 Επικοινωνίες & Ατζέντηδες"])

    with sub_sl:
        mc1, mc2, mc3 = st.columns([2.5, 1, 2])
        new_sl_name = mc1.text_input("Νέα Shortlist", placeholder="π.χ. Στόχοι Ελλάδα 2027", label_visibility="collapsed")
        if mc2.button("➕ Δημιουργία", width="stretch"):
            if new_sl_name.strip() and new_sl_name.strip() not in crm["shortlists"]:
                crm["shortlists"].append(new_sl_name.strip())
                persist_crm(f"Δημιουργήθηκε η shortlist «{new_sl_name.strip()}»!")
                st.rerun()

        if not crm["shortlists"]:
            st.info("Δεν έχεις ακόμα καμία shortlist.")
        else:
            active_sl = st.selectbox("Επίλεξε Shortlist για προβολή", crm["shortlists"], key="view_sl_box")
            sl_df = df[df["shortlists_list"].apply(lambda lst: active_sl in lst)]

            head_sl1, head_sl2 = st.columns([4, 1.2])
            head_sl1.subheader(f"📌 {active_sl} ({len(sl_df)} αθλητές)")
            if head_sl2.button("🗑️ Διαγραφή λίστας", key="del_sl_btn"):
                crm["shortlists"] = [s for s in crm["shortlists"] if s != active_sl]
                for entry in crm["players"].values():
                    entry["shortlists"] = [s for s in (entry.get("shortlists") or []) if s != active_sl]
                persist_crm(f"Διαγράφηκε η shortlist «{active_sl}».")
                st.rerun()

            if sl_df.empty:
                st.info("Η λίστα είναι άδεια. Πρόσθεσε αθλητές από τη «Βάση Αθλητών» ή την «Καρτέλα Αθλητή».")
            else:
                sl_cols = [
                    "name", "team", "league", "position", "height_cm", "age",
                    "sets", "points_per_set", "aces_per_set", "blocks_per_set", "attack_pct",
                    "contact_status", "agent_name", "contract_until", "notes", "profile_url",
                ]
                render_styled_table(sl_df, sl_cols, COMMON_CFG)

                rc1, rc2 = st.columns([3, 1.2])
                rem_map = {f"{r['name']} — {r['team']}": r["player_id"] for _, r in sl_df.iterrows()}
                rem_pick = rc1.selectbox("Αφαίρεση αθλητή από αυτή τη λίστα", list(rem_map.keys()), key="rem_sl_pick")
                if rc2.button("Αφαίρεση", width="stretch", key="rem_sl_btn"):
                    rpid = rem_map[rem_pick]
                    crm["players"][rpid]["shortlists"] = [s for s in crm["players"][rpid].get("shortlists", []) if s != active_sl]
                    persist_crm("Ο αθλητής αφαιρέθηκε από τη shortlist.")
                    st.rerun()

                st.download_button(
                    f"⬇️ Εξαγωγή «{active_sl}» σε Excel / CSV",
                    data=sl_df[sl_cols].to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"shortlist_{active_sl}.csv",
                    mime="text/csv",
                )

    with sub_ours:
        ours_df = df[df["is_our_client"]]
        if ours_df.empty:
            st.info("Δεν έχεις μαρκάρει ακόμα δικούς σας αθλητές. Πήγαινε στην «Καρτέλα & CRM Αθλητή» και τικάρισε το «⭐ Αθλητής της εταιρείας μας (MSA)».")
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
            st.info("Δεν έχεις καταχωρήσει ακόμα επικοινωνίες ή στοιχεία ατζέντηδων.")
        else:
            pipe_cols = [
                "name", "team", "league", "position", "contact_status",
                "phone", "email", "social", "has_agent", "agent_name", "contract_until", "shortlists_str", "notes",
            ]
            render_styled_table(pipe_df, pipe_cols, COMMON_CFG)
