import json
import os

import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Volley Scouting Dashboard",
    page_icon="🏐",
    layout="wide",
)

DATA_PATH = "data/players.csv"
META_PATH = "data/last_updated.json"


@st.cache_data(ttl=300)
def load_data():
    if not os.path.exists(DATA_PATH):
        return pd.DataFrame(), {}
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

    for col in ["name", "team", "league", "country", "position", "nationality", "profile_url", "photo_url"]:
        if col in df.columns:
            df[col] = df[col].fillna("").astype(str)

    return df, meta


df, meta = load_data()

st.title("🏐 Volley Scouting Dashboard")

if df.empty:
    st.warning("Δεν υπάρχουν ακόμα δεδομένα. Τρέξε το Daily Volleyball Stats Update από το GitHub Actions.")
    st.stop()

last_upd = meta.get("last_updated_utc", "—")
st.caption(f"Τελευταία αυτόματη ενημέρωση: **{last_upd}**  |  Σύνολο αθλητών στη βάση: **{len(df)}**")

# -------------------- SIDEBAR FILTERS --------------------
st.sidebar.header("🔍 Φίλτρα Scouting")

search_name = st.sidebar.text_input("Αναζήτηση αθλητή / ομάδας", placeholder="π.χ. Balashov ή Galati...")

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
    "Εμφάνιση και όσων δεν έχουν δηλωμένο ύψος (0 cm)",
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
tab_leaders, tab_db, tab_card = st.tabs([
    "🏆 Καθημερινές Κατατάξεις",
    "📋 Βάση Αθλητών & Φίλτρα",
    "👤 Καρτέλα Αθλητή",
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
        def show_board(title, data, sort_col, extra_cols):
            st.markdown(f"### {title}")
            cols = ["name", "team", "position", "height_cm", "age", "sets"] + extra_cols
            view = data.sort_values(by=sort_col, ascending=False).head(top_n)[cols].reset_index(drop=True)
            view.index = view.index + 1
            st.dataframe(
                view,
                use_container_width=True,
                column_config={
                    "name": "Αθλητής",
                    "team": "Ομάδα",
                    "position": "Θέση",
                    "height_cm": st.column_config.NumberColumn("Ύψος", format="%d cm"),
                    "age": "Ηλικία",
                    "sets": "Σετ",
                    "points": "Πόντοι",
                    "points_per_set": st.column_config.NumberColumn("Πόντοι/Σετ", format="%.2f"),
                    "aces": "Άσοι",
                    "aces_per_set": st.column_config.NumberColumn("Άσοι/Σετ", format="%.2f"),
                    "serve_total": "Σερβίς",
                    "blocks": "Μπλοκ",
                    "blocks_per_set": st.column_config.NumberColumn("Μπλοκ/Σετ", format="%.2f"),
                    "attack_total": "Επιθέσεις",
                    "attack_points": "Επιθ. Π.",
                    "attack_pct": st.column_config.NumberColumn("Επίθεση %", format="%.1f%%"),
                    "attack_eff_pct": st.column_config.NumberColumn("Αποτελ. %", format="%.1f%%"),
                    "reception_total": "Υποδοχές",
                    "reception_pos_pct": st.column_config.NumberColumn("Θετ. Υπ. %", format="%.1f%%"),
                    "reception_perf_pct": st.column_config.NumberColumn("Άριστη %", format="%.1f%%"),
                },
            )

        col_a, col_b = st.columns(2)
        with col_a:
            s_col = "points_per_set" if rank_mode == "Ανά Σετ (μ.ό.)" else "points"
            show_board("🔥 Καλύτερος Σκόρερ", ldf, s_col, ["points", "points_per_set"])

            b_col = "blocks_per_set" if rank_mode == "Ανά Σετ (μ.ό.)" else "blocks"
            show_board("🧱 Καλύτερος Μπλοκέρ", ldf, b_col, ["blocks", "blocks_per_set"])

        with col_b:
            sv_col = "aces_per_set" if rank_mode == "Ανά Σετ (μ.ό.)" else "aces"
            show_board("🎯 Καλύτερος Σέρβερ", ldf, sv_col, ["aces", "aces_per_set", "serve_total"])

            atk_df = ldf[ldf["attack_total"] >= 15]
            if atk_df.empty:
                atk_df = ldf[ldf["attack_total"] > 0]
            show_board(
                "⚡ Καλύτερος Επιθετικός (min 15 επιθ.)",
                atk_df,
                "attack_pct",
                ["attack_pct", "attack_eff_pct", "attack_points", "attack_total"],
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
        "reception_pos_pct", "reception_perf_pct", "profile_url",
    ]
    available_cols = [c for c in display_cols if c in fdf.columns]
    st.dataframe(
        fdf[available_cols],
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "name": "Αθλητής",
            "team": "Ομάδα",
            "league": "Πρωτάθλημα",
            "position": "Θέση",
            "height_cm": st.column_config.NumberColumn("Ύψος", format="%d cm"),
            "age": "Ηλικία",
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
        options = fdf.sort_values("name")["name"] + " — " + fdf.sort_values("name")["team"]
        sorted_fdf = fdf.sort_values("name").reset_index(drop=True)
        chosen = st.selectbox("Διάλεξε αθλητή", options.tolist())
        idx = options.tolist().index(chosen)
        p = sorted_fdf.iloc[idx]

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
