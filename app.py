import json
import os

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="MSA Volley Scouting",
    page_icon="🏐",
    layout="wide",
)

DATA_PATH = "data/players.csv"
META_PATH = "data/last_updated.json"
OVERRIDES_PATH = "data/manual_overrides.csv"


def get_app_password():
    try:
        return str(st.secrets.get("APP_PASSWORD", "")).strip()
    except Exception:
        return ""


def check_password():
    """Προστασία με κωδικό από τα Streamlit Secrets (δεν φαίνεται στον δημόσιο κώδικα)."""
    app_pwd = get_app_password()
    if not app_pwd:
        return False  # Δεν έχει οριστεί ακόμα κωδικός

    if st.session_state.get("authenticated", False):
        return True

    st.markdown("<br><br>", unsafe_allow_html=True)
    _, center, _ = st.columns([1, 1.4, 1])
    with center:
        st.title("🏐 MSA Volley Scouting")
        st.caption("Περιορισμένη πρόσβαση — Εισάγετε τον κωδικό σας.")
        with st.form("login_form"):
            entered = st.text_input("Κωδικός πρόσβασης", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("Είσοδος", use_container_width=True)
            if submitted:
                if entered == app_pwd:
                    st.session_state["authenticated"] = True
                    st.rerun()
                else:
                    st.error("Λάθος κωδικός. Δοκιμάστε ξανά.")
    st.stop()


is_protected = check_password()


@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(DATA_PATH):
        return pd.DataFrame(), {}, pd.DataFrame()

    df = pd.read_csv(DATA_PATH)
    meta = {}
    if os.path.exists(META_PATH):
        with open(META_PATH, "r", encoding="utf-8") as f:
            meta = json.load(f)

    num_cols = [
        "height_cm", "birth_year", "age", "matches", "sets", "points",
        "points_per_set", "break_points", "won_lost", "serve_total", "aces",
        "aces_per_set", "serve_errors", "blocks", "blocks_per_set",
        "attack_total", "attack_points", "attack_errors", "attack_blocked",
        "attack_pct", "attack_eff_pct", "reception_total",
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

    df["raw_height_cm"] = df["height_cm"].astype(int)
    df["notes"] = ""

    overrides_df = pd.DataFrame(columns=["player_id", "name", "team", "height_cm", "position", "notes"])
    if os.path.exists(OVERRIDES_PATH):
        try:
            ov = pd.read_csv(OVERRIDES_PATH)
            if "player_id" in ov.columns:
                overrides_df = ov.copy()
                ov_map = ov.set_index("player_id").to_dict(orient="index")
                for idx, row in df.iterrows():
                    pid = row["player_id"]
                    if pid in ov_map:
                        h = ov_map[pid].get("height_cm", 0)
                        if pd.notna(h) and int(float(h)) > 0:
                            df.at[idx, "height_cm"] = int(float(h))
                        pos = ov_map[pid].get("position", "")
                        if pd.notna(pos) and str(pos).strip():
                            df.at[idx, "position"] = str(pos).strip()
                        note = ov_map[pid].get("notes", "")
                        if pd.notna(note) and str(note).strip():
                            df.at[idx, "notes"] = str(note).strip()
        except Exception:
            pass

    return df, meta, overrides_df


df, meta, overrides_df = load_data()

# Header
head_left, head_right = st.columns([5, 1])
with head_left:
    st.title("🏐 MSA Volley Scouting")
with head_right:
    if is_protected:
        if st.button("🔒 Έξοδος", use_container_width=True):
            st.session_state["authenticated"] = False
            st.rerun()

if not is_protected:
    st.info(
        "🔓 **Ο κωδικός εισόδου δεν έχει ενεργοποιηθεί ακόμα.** "
        "Πάτα **Manage app** κάτω δεξιά -> **⋮ -> Settings -> Secrets** και πρόσθεσε: `APP_PASSWORD = \"ο_κωδικός_σου\"`."
    )

if df.empty:
    st.warning("Δεν υπάρχουν ακόμα δεδομένα. Τρέξε το Daily Volleyball Stats Update από το GitHub Actions.")
    st.stop()

last_upd = meta.get("last_updated_utc", "—")
st.caption(f"Τελευταία αυτόματη ενημέρωση: **{last_upd}**  |  Σύνολο αθλητών στη βάση: **{len(df)}**")

# -------------------- SIDEBAR FILTERS --------------------
st.sidebar.header("🔍 Φίλτρα Scouting")

search_name = st.sidebar.text_input("Αναζήτηση αθλητή / ομάδας", placeholder="π.χ. Jovovic ή Galati...")

genders_opt = sorted([x for x in df["gender"].unique() if x])
if len(genders_opt) > 1:
    sel_genders = st.sidebar.multiselect("Κατηγορία (Άνδρες / Γυναίκες)", genders_opt, default=genders_opt)
else:
    sel_genders = genders_opt

leagues_opt = sorted([x for x in df["league"].unique() if x])
sel_leagues = st.sidebar.multiselect("Πρωτάθλημα", leagues_opt, default=leagues_opt)

positions_opt = sorted([x for x in df["position"].unique() if x])
sel_positions = st.sidebar.multiselect("Θέση", positions_opt, default=positions_opt)

teams_opt = sorted([x for x in df["team"].unique() if x])
sel_teams = st.sidebar.multiselect("Ομάδα (προαιρετικό)", teams_opt)

nations_opt = sorted([x for x in df["nationality"].unique() if x])
sel_nations = st.sidebar.multiselect("Εθνικότητα (προαιρετικό)", nations_opt)

st.sidebar.markdown("---")
st.sidebar.subheader("📏 Σωματότυπος & Ηλικία")

min_height = st.sidebar.slider("Ελάχιστο Ύψος (cm)", min_value=165, max_value=220, value=165, step=1)
include_unknown_height = st.sidebar.checkbox(
    "Εμφάνιση και όσων δεν έχουν δηλωμένο ύψος",
    value=(min_height == 165),
    help="Απενεργοποίησέ το όταν ψάχνεις αυστηρά παίκτες πάνω από συγκεκριμένο ύψος.",
)

age_range = st.sidebar.slider("Ηλικία (έτη)", min_value=15, max_value=45, value=(15, 45))
include_unknown_age = st.sidebar.checkbox("Εμφάνιση όσων δεν έχουν ημ/νία γέννησης", value=True)

st.sidebar.markdown("---")
st.sidebar.subheader("📊 Ελάχιστα Στατιστικά")
min_sets = st.sidebar.number_input("Ελάχιστα Σετ", min_value=0, max_value=200, value=0, step=1)
min_pts_set = st.sidebar.number_input("Ελάχιστοι Πόντοι / Σετ", min_value=0.0, max_value=10.0, value=0.0, step=0.2)
min_aces_set = st.sidebar.number_input("Ελάχιστοι Άσοι / Σετ", min_value=0.0, max_value=3.0, value=0.0, step=0.05)
min_blk_set = st.sidebar.number_input("Ελάχιστα Μπλοκ / Σετ", min_value=0.0, max_value=3.0, value=0.0, step=0.05)

# Apply filters
fdf = df.copy()
if sel_genders:
    fdf = fdf[fdf["gender"].isin(sel_genders)]
if sel_leagues:
    fdf = fdf[fdf["league"].isin(sel_leagues)]
if sel_positions:
    fdf = fdf[fdf["position"].isin(sel_positions)]
if sel_teams:
    fdf = fdf[fdf["team"].isin(sel_teams)]
if sel_nations:
    fdf = fdf[fdf["nationality"].isin(sel_nations)]

if search_name.strip():
    q = search_name.strip().lower()
    fdf = fdf[
        fdf["name"].str.lower().str.contains(q, na=False)
        | fdf["team"].str.lower().str.contains(q, na=False)
        | fdf["notes"].str.lower().str.contains(q, na=False)
    ]

if include_unknown_height:
    fdf = fdf[(fdf["height_cm"] >= min_height) | (fdf["height_cm"] == 0)]
else:
    fdf = fdf[fdf["height_cm"] >= min_height]

if include_unknown_age:
    fdf = fdf[((fdf["age"] >= age_range[0]) & (fdf["age"] <= age_range[1])) | (fdf["age"] == 0)]
else:
    fdf = fdf[(fdf["age"] >= age_range[0]) & (fdf["age"] <= age_range[1])]

fdf = fdf[
    (fdf["sets"] >= min_sets)
    & (fdf["points_per_set"] >= min_pts_set)
    & (fdf["aces_per_set"] >= min_aces_set)
    & (fdf["blocks_per_set"] >= min_blk_set)
]

# Top KPIs
k1, k2, k3, k4 = st.columns(4)
k1.metric("Αθλητές (με φίλτρα)", len(fdf))
k2.metric("Με καταχωρημένο ύψος", int((fdf["height_cm"] > 0).sum()))
k3.metric("Κορυφαίος Σκόρερ", f"{int(fdf['points'].max())} π." if not fdf.empty else "—")
k4.metric("Κορυφαίος Μπλοκέρ", f"{int(fdf['blocks'].max())} μπλ." if not fdf.empty else "—")

# -------------------- TABS --------------------
tab_leaders, tab_db, tab_card, tab_overrides = st.tabs([
    "🏆 Καθημερινές Κατατάξεις",
    "📋 Βάση Αθλητών & Φίλτρα",
    "👤 Καρτέλα Αθλητή",
    "✏️ Ύψη & Σημειώσεις",
])

with tab_leaders:
    st.subheader("Κορυφαίοι ανά Κατηγορία")
    c_opt1, c_opt2, c_opt3 = st.columns(3)
    top_n = c_opt1.slider("Πλήθος παικτών ανά στήλη", 5, 30, 10)
    lb_min_sets = c_opt2.number_input("Ελάχιστα σετ για κατατάξεις", min_value=1, max_value=100, value=3)
    rank_mode = c_opt3.radio("Ταξινόμηση", ["Ανά Σετ (μ.ό.)", "Σύνολο"], horizontal=True)

    ldf = fdf[fdf["sets"] >= lb_min_sets].copy()

    if ldf.empty:
        st.info("Δεν βρέθηκαν αθλητές με αυτά τα κριτήρια. Μείωσε τα ελάχιστα σετ ή άνοιξε τα φίλτρα αριστερά.")
    else:
        def show_board(title, data, sort_col, stat_cols):
            st.markdown(f"### {title}")
            # Τα βασικά νούμερα ακριβώς δίπλα στο όνομα — μηδέν scroll!
            cols = ["name"] + stat_cols + ["team", "position", "height_cm", "sets"]
            view = data.sort_values(by=sort_col, ascending=False).head(top_n)[cols].reset_index(drop=True)
            view["height_cm"] = view["height_cm"].replace(0, pd.NA)
            view.index = view.index + 1
            st.dataframe(
                view,
                use_container_width=True,
                column_config={
                    "name": st.column_config.TextColumn("Αθλητής", width="medium"),
                    "team": st.column_config.TextColumn("Ομάδα", width="medium"),
                    "position": st.column_config.TextColumn("Θέση", width="small"),
                    "height_cm": st.column_config.NumberColumn("Ύψος", format="%d cm", width="small"),
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
                },
            )

        col_a, col_b = st.columns(2)
        with col_a:
            if rank_mode == "Ανά Σετ (μ.ό.)":
                show_board("🔥 Καλύτερος Σκόρερ", ldf, "points_per_set", ["points_per_set", "points"])
                show_board("🧱 Καλύτερος Μπλοκέρ", ldf, "blocks_per_set", ["blocks_per_set", "blocks"])
            else:
                show_board("🔥 Καλύτερος Σκόρερ", ldf, "points", ["points", "points_per_set"])
                show_board("🧱 Καλύτερος Μπλοκέρ", ldf, "blocks", ["blocks", "blocks_per_set"])

        with col_b:
            if rank_mode == "Ανά Σετ (μ.ό.)":
                show_board("🎯 Καλύτερος Σέρβερ", ldf, "aces_per_set", ["aces_per_set", "aces"])
            else:
                show_board("🎯 Καλύτερος Σέρβερ", ldf, "aces", ["aces", "aces_per_set"])

            atk_df = ldf[ldf["attack_total"] >= 15]
            if atk_df.empty:
                atk_df = ldf[ldf["attack_total"] > 0]
            show_board(
                "⚡ Καλύτερος Επιθετικός (min 15 επιθ.)",
                atk_df,
                "attack_pct",
                ["attack_pct", "attack_eff_pct", "attack_points"],
            )

        rec_df = ldf[ldf["reception_total"] >= 15]
        if not rec_df.empty:
            show_board(
                "🛡️ Καλύτερος Υποδοχέας (min 15 υποδοχές)",
                rec_df,
                "reception_pos_pct",
                ["reception_pos_pct", "reception_perf_pct", "reception_total"],
            )

with tab_db:
    st.subheader("Πλήρης Βάση Δεδομένων Αθλητών")
    display_cols = [
        "name", "team", "league", "position", "height_cm", "age", "nationality",
        "matches", "sets", "points", "points_per_set",
        "aces", "aces_per_set", "blocks", "blocks_per_set",
        "attack_points", "attack_pct", "attack_eff_pct",
        "reception_pos_pct", "reception_perf_pct", "notes", "profile_url",
    ]
    available_cols = [c for c in display_cols if c in fdf.columns]
    db_view = fdf[available_cols].copy()
    db_view["height_cm"] = db_view["height_cm"].replace(0, pd.NA)
    db_view["age"] = db_view["age"].replace(0, pd.NA)

    st.dataframe(
        db_view,
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "name": "Αθλητής",
            "team": "Ομάδα",
            "league": "Πρωτάθλημα",
            "position": "Θέση",
            "height_cm": st.column_config.NumberColumn("Ύψος", format="%d cm"),
            "age": st.column_config.NumberColumn("Ηλικία", format="%d"),
            "nationality": "Εθν.",
            "matches": "Αγ.",
            "sets": "Σετ",
            "points": "Πόντοι",
            "points_per_set": st.column_config.NumberColumn("Π./Σετ", format="%.2f"),
            "aces": "Άσοι",
            "aces_per_set": st.column_config.NumberColumn("Άσοι/Σετ", format="%.2f"),
            "blocks": "Μπλοκ",
            "blocks_per_set": st.column_config.NumberColumn("Μπλ./Σετ", format="%.2f"),
            "attack_points": "Επιθ. Π.",
            "attack_pct": st.column_config.NumberColumn("Επίθ. %", format="%.1f%%"),
            "attack_eff_pct": st.column_config.NumberColumn("Αποτ. %", format="%.1f%%"),
            "reception_pos_pct": st.column_config.NumberColumn("Υπ. Θετ.%", format="%.1f%%"),
            "reception_perf_pct": st.column_config.NumberColumn("Υπ. Άρ.%", format="%.1f%%"),
            "notes": "Σημειώσεις",
            "profile_url": st.column_config.LinkColumn("Προφίλ", display_text="Άνοιγμα ↗"),
        },
    )

    csv_export = fdf[available_cols].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ Κατέβασμα φιλτραρισμένης λίστας (Excel / CSV)",
        data=csv_export,
        file_name="volley_scouting_filtered.csv",
        mime="text/csv",
    )

with tab_card:
    st.subheader("Αναλυτική Καρτέλα Αθλητή")
    if fdf.empty:
        st.info("Δεν υπάρχουν αθλητές με τα τρέχοντα φίλτρα.")
    else:
        sorted_fdf = fdf.sort_values("name").reset_index(drop=True)
        options = (sorted_fdf["name"] + " — " + sorted_fdf["team"]).tolist()
        chosen = st.selectbox("Διάλεξε αθλητή", options)
        p = sorted_fdf.iloc[options.index(chosen)]

        c1, c2 = st.columns([1, 3])
        with c1:
            if p["photo_url"] and "noimage" not in p["photo_url"].lower():
                st.image(p["photo_url"], width=180)
            st.markdown(f"**Ομάδα:** {p['team']}")
            st.markdown(f"**Πρωτάθλημα:** {p['league']}")
            st.markdown(f"**Θέση:** {p['position']}")
            st.markdown(f"**Ύψος:** {int(p['height_cm'])} cm" if p["height_cm"] > 0 else "**Ύψος:** Μη δηλωμένο")
            st.markdown(f"**Ηλικία:** {int(p['age'])} ({int(p['birth_year'])})" if p["age"] > 0 else "**Ηλικία:** —")
            st.markdown(f"**Εθνικότητα:** {p['nationality']}")
            if p["notes"]:
                st.info(f"📝 **Σημείωση:** {p['notes']}")
            if p["profile_url"]:
                st.link_button("Επίσημο Προφίλ ↗", p["profile_url"])

        with c2:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Αγώνες / Σετ", f"{int(p['matches'])} / {int(p['sets'])}")
            m2.metric("Πόντοι (μ.ό.)", f"{int(p['points'])} ({p['points_per_set']:.2f})")
            m3.metric("Άσοι (μ.ό.)", f"{int(p['aces'])} ({p['aces_per_set']:.2f})")
            m4.metric("Μπλοκ (μ.ό.)", f"{int(p['blocks'])} ({p['blocks_per_set']:.2f})")

            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Επίθεση %", f"{p['attack_pct']:.1f}%")
            m6.metric("Αποτελεσματ. %", f"{p['attack_eff_pct']:.1f}%")
            m7.metric("Θετική Υποδοχή %", f"{p['reception_pos_pct']:.1f}%")
            m8.metric("Άριστη Υποδοχή %", f"{p['reception_perf_pct']:.1f}%")

with tab_overrides:
    st.subheader("✏️ Συμπλήρωση Υψών & Προσωπικών Σημειώσεων")
    st.markdown(
        "Επειδή κάποιες ομοσπονδίες δεν περνάνε το ύψος όλων των αθλητών, μπορείς να το συμπληρώσεις εδώ "
        "(μαζί με δικές σου σημειώσεις, π.χ. *«Ελεύθερος το καλοκαίρι»* ή *«Πολύ καλό άλμα»*). "
        "Ό,τι αποθηκεύσεις στο `data/manual_overrides.csv` **μένει μόνιμα** και δεν σβήνεται ποτέ από τις καθημερινές ανανεώσεις."
    )

    only_missing = st.checkbox("Εμφάνιση μόνο αθλητών χωρίς ύψος", value=True)
    edit_source = df[df["height_cm"] == 0].copy() if only_missing else df.copy()
    edit_source = edit_source.sort_values(by=["points", "name"], ascending=[False, True])

    editor_cols = ["player_id", "name", "team", "position", "height_cm", "notes"]
    edited_df = st.data_editor(
        edit_source[editor_cols],
        use_container_width=True,
        hide_index=True,
        height=420,
        disabled=["player_id", "name", "team"],
        column_config={
            "player_id": st.column_config.TextColumn("ID", width="small"),
            "name": "Αθλητής",
            "team": "Ομάδα",
            "position": st.column_config.SelectboxColumn(
                "Θέση",
                options=["Setter", "Opposite", "Outside Hitter", "Middle Blocker", "Libero", "Unknown"],
            ),
            "height_cm": st.column_config.NumberColumn("Ύψος (cm)", min_value=0, max_value=235, step=1),
            "notes": st.column_config.TextColumn("Σημειώσεις Agent", width="large"),
        },
    )

    # Συγχώνευση παλιών overrides με όσα μόλις συμπλήρωσες
    merged_records = {}
    if not overrides_df.empty and "player_id" in overrides_df.columns:
        for _, r in overrides_df.iterrows():
            pid = str(r["player_id"])
            merged_records[pid] = {
                "player_id": pid,
                "name": str(r.get("name", "")),
                "team": str(r.get("team", "")),
                "height_cm": int(float(r["height_cm"])) if pd.notna(r.get("height_cm")) else 0,
                "position": str(r.get("position", "")) if pd.notna(r.get("position")) else "",
                "notes": str(r.get("notes", "")) if pd.notna(r.get("notes")) else "",
            }

    raw_height_map = df.set_index("player_id")["raw_height_cm"].to_dict()
    for _, r in edited_df.iterrows():
        pid = str(r["player_id"])
        h_new = int(r["height_cm"]) if pd.notna(r["height_cm"]) else 0
        note_new = str(r["notes"]).strip() if pd.notna(r["notes"]) else ""
        h_raw = int(raw_height_map.get(pid, 0))
        if (h_new > 0 and h_new != h_raw) or note_new or (pid in merged_records):
            merged_records[pid] = {
                "player_id": pid,
                "name": str(r["name"]),
                "team": str(r["team"]),
                "height_cm": h_new,
                "position": str(r["position"]),
                "notes": note_new,
            }

    if merged_records:
        out_df = pd.DataFrame(list(merged_records.values()))
        csv_text = out_df.to_csv(index=False)
        st.success(f"Έτοιμες **{len(out_df)}** καταχωρήσεις για μόνιμη αποθήκευση.")
        st.markdown(
            "**Πώς τις αποθηκεύεις μόνιμα (10 δευτερόλεπτα):** Πάτα το εικονίδιο αντιγραφής πάνω δεξιά στο πλαίσιο παρακάτω "
            "και κάνε paste στο αρχείο `data/manual_overrides.csv` στο GitHub."
        )
        st.code(csv_text, language="csv")
        st.download_button(
            "⬇️ Ή κατέβασε το manual_overrides.csv",
            data=csv_text.encode("utf-8-sig"),
            file_name="manual_overrides.csv",
            mime="text/csv",
        )
    else:
        st.caption("Συμπλήρωσε ένα ύψος ή μια σημείωση στον πίνακα παραπάνω για να δημιουργηθεί το αρχείο αποθήκευσης.")
