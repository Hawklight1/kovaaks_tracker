from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st
from pymongo import MongoClient


DEFAULT_MONGO_URI = "mongodb://localhost:27017/"
DEFAULT_DB_NAME = "kovaaks_tracker"
DEFAULT_RUNS_COLLECTION = "runs"
DEFAULT_KILL_EVENTS_COLLECTION = "kill_events"
DEFAULT_PLAYLISTS_COLLECTION = "playlists"


@st.cache_resource
def get_client(mongo_uri: str) -> MongoClient:
    return MongoClient(mongo_uri)


@st.cache_data(ttl=30)
def load_runs_df(mongo_uri: str, db_name: str, collection_name: str) -> pd.DataFrame:
    client = get_client(mongo_uri)
    docs = list(client[db_name][collection_name].find({}, {"raw_summary": 0}))
    if not docs:
        return pd.DataFrame()

    df = pd.DataFrame(docs)

    if "scenario_name" in df.columns:
        df["scenario_name"] = (
            df["scenario_name"]
            .fillna("Unknown Scenario")
            .astype(str)
            .str.split(" - ")
            .str[0]
            .str.strip()
        )
    else:
        df["scenario_name"] = "Unknown Scenario"

    if "challenge_name" in df.columns:
        df["challenge_name"] = (
            df["challenge_name"]
            .fillna(df["scenario_name"])
            .astype(str)
            .str.strip()
        )
    else:
        df["challenge_name"] = df["scenario_name"]

    if "run_datetime" in df.columns:
        df["challenge_start_dt"] = pd.to_datetime(df["run_datetime"], errors="coerce")
    elif "challenge_start" in df.columns:
        df["challenge_start_dt"] = pd.to_datetime(df["challenge_start"], errors="coerce")
    else:
        df["challenge_start_dt"] = pd.NaT

    numeric_cols = [
        "score",
        "kills",
        "deaths",
        "fight_time",
        "time_remaining",
        "avg_ttk",
        "damage_done",
        "hit_count",
        "miss_count",
        "pause_count",
        "pause_duration",
        "avg_target_scale",
        "avg_time_dilation",
        "input_lag",
        "max_fps_config",
        "kill_count_parsed",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    total_shots = (
        df.get("hit_count", pd.Series(index=df.index, dtype=float)).fillna(0)
        + df.get("miss_count", pd.Series(index=df.index, dtype=float)).fillna(0)
    )
    df["accuracy_pct"] = (
        df.get("hit_count", pd.Series(index=df.index, dtype=float)) / total_shots * 100
    ).where(total_shots > 0)

    if "scenario_hash" not in df.columns:
        df["scenario_hash"] = None

    if "file_metadata" in df.columns:
        df["filename_group"] = df["file_metadata"].apply(
            lambda x: x.get("challenge_name_from_file") if isinstance(x, dict) else None
        )
    else:
        df["filename_group"] = None

    return df


@st.cache_data(ttl=30)
def load_kill_events_df(
    mongo_uri: str,
    db_name: str,
    collection_name: str,
    run_ids: list[str],
) -> pd.DataFrame:
    if not run_ids:
        return pd.DataFrame()

    client = get_client(mongo_uri)
    docs = list(client[db_name][collection_name].find({"run_id": {"$in": run_ids}}, {"_id": 0}))
    if not docs:
        return pd.DataFrame()

    df = pd.DataFrame(docs)
    numeric_cols = [
        "kill_number",
        "ttk_seconds",
        "shots",
        "hits",
        "accuracy",
        "damage_done",
        "damage_possible",
        "efficiency",
        "cheated",
        "overshots",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=30)
def load_playlists_df(mongo_uri: str, db_name: str, collection_name: str) -> pd.DataFrame:
    client = get_client(mongo_uri)
    docs = list(
        client[db_name][collection_name].find(
            {},
            {
                "_id": 1,
                "playlist_name": 1,
                "scenario_names": 1,
                "scenario_count": 1,
                "source_file": 1,
                "updated": 1,
                "author_name": 1,
            },
        )
    )
    if not docs:
        return pd.DataFrame(
            columns=[
                "_id",
                "playlist_name",
                "scenario_names",
                "scenario_count",
                "source_file",
                "updated",
                "author_name",
            ]
        )

    df = pd.DataFrame(docs)
    if "playlist_name" not in df.columns:
        df["playlist_name"] = "Unnamed Playlist"
    if "scenario_names" not in df.columns:
        df["scenario_names"] = [[] for _ in range(len(df))]
    if "scenario_count" not in df.columns:
        df["scenario_count"] = df["scenario_names"].apply(
            lambda x: len(x) if isinstance(x, list) else 0
        )
    return df


def build_custom_group_lookup(text_blob: str) -> dict[str, str]:
    scenario_to_group: dict[str, str] = {}
    current_group: str | None = None

    for raw_line in text_blob.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.endswith(":"):
            current_group = line[:-1].strip()
            continue

        if current_group:
            scenario_to_group[line] = current_group

    return scenario_to_group


def build_custom_group_order_lookup(text_blob: str) -> dict[str, list[str]]:
    group_order_lookup: dict[str, list[str]] = {}
    current_group: str | None = None

    for raw_line in text_blob.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.endswith(":"):
            current_group = line[:-1].strip()
            if current_group and current_group not in group_order_lookup:
                group_order_lookup[current_group] = []
            continue

        if current_group:
            group_order_lookup.setdefault(current_group, []).append(line)

    return group_order_lookup


def build_playlist_lookup(playlists_df: pd.DataFrame) -> dict[str, set[str]]:
    playlist_lookup: dict[str, set[str]] = {}

    if playlists_df.empty:
        return playlist_lookup

    for _, row in playlists_df.iterrows():
        playlist_name = str(row.get("playlist_name", "")).strip()
        scenarios = row.get("scenario_names", [])

        if not playlist_name:
            continue
        if not isinstance(scenarios, list):
            continue

        cleaned_scenarios = {
            str(s).strip()
            for s in scenarios
            if pd.notna(s) and str(s).strip()
        }

        playlist_lookup[playlist_name] = cleaned_scenarios

    return playlist_lookup

def build_playlist_order_lookup(playlists_df: pd.DataFrame) -> dict[str, list[str]]:
    playlist_order_lookup: dict[str, list[str]] = {}

    if playlists_df.empty:
        return playlist_order_lookup

    for _, row in playlists_df.iterrows():
        playlist_name = str(row.get("playlist_name", "")).strip()
        scenarios = row.get("scenario_names", [])

        if not playlist_name:
            continue
        if not isinstance(scenarios, list):
            continue

        cleaned_scenarios: list[str] = []
        for s in scenarios:
            scenario_name = str(s).strip()
            if scenario_name:
                cleaned_scenarios.append(scenario_name)

        playlist_order_lookup[playlist_name] = cleaned_scenarios

    return playlist_order_lookup

def summarize_tasks(
    df: pd.DataFrame,
    scenario_order: list[str] | None = None,
) -> pd.DataFrame:
    grouped = (
        df.groupby("scenario_name", dropna=False)
        .agg(
            runs=("_id", "count"),
            best_score=("score", "max"),
            latest_run=("challenge_start_dt", "max"),
        )
        .reset_index()
    )

    if scenario_order:
        order_map = {name: i for i, name in enumerate(scenario_order)}
        grouped["order_index"] = grouped["scenario_name"].map(order_map)
        grouped["order_index"] = grouped["order_index"].fillna(len(order_map))
        grouped = grouped.sort_values(["order_index", "scenario_name"], ascending=[True, True])
    else:
        grouped = grouped.sort_values(["runs", "best_score"], ascending=[False, False])

    return grouped


def render_performance_chart(task_df: pd.DataFrame, metric_col: str = "score") -> None:
    chart_df = task_df.copy()
    chart_df = chart_df.dropna(subset=["challenge_start_dt", metric_col]).sort_values("challenge_start_dt")

    if chart_df.empty:
        st.info("No dated performance data available for that scenario.")
        return

    chart_df["run_date"] = pd.to_datetime(chart_df["challenge_start_dt"])

    chart = (
        alt.Chart(chart_df)
        .mark_line(point=True)
        .encode(
            x=alt.X(
                "run_date:T",
                title="Run Date",
                axis=alt.Axis(format="%Y-%m-%d", labelAngle=-35),
            ),
            y=alt.Y(f"{metric_col}:Q", title=metric_col.replace("_", " ").title()),
            tooltip=[
                alt.Tooltip("scenario_name:N", title="Scenario"),
                alt.Tooltip("run_date:T", title="Run Time"),
                alt.Tooltip(f"{metric_col}:Q", title=metric_col.replace("_", " ").title(), format=".2f"),
                alt.Tooltip("score:Q", title="Score", format=".2f"),
                alt.Tooltip("accuracy_pct:Q", title="Accuracy %", format=".1f"),
                alt.Tooltip("avg_ttk:Q", title="Avg TTK", format=".3f"),
                alt.Tooltip("source_file:N", title="Source File"),
            ],
        )
        .properties(height=400)
        .interactive()
    )

    st.altair_chart(chart, use_container_width=True)


st.set_page_config(page_title="Kovaak's Task Dashboard", layout="wide")
st.title("Kovaak's Task Dashboard")
st.caption("Browse tasks by group, inspect one scenario at a time, and review run history.")

with st.sidebar:
    st.header("Connection")
    mongo_uri = st.text_input("Mongo URI", value=DEFAULT_MONGO_URI)
    db_name = st.text_input("Database", value=DEFAULT_DB_NAME)
    runs_collection = st.text_input("Runs collection", value=DEFAULT_RUNS_COLLECTION)
    kill_events_collection = st.text_input("Kill events collection", value=DEFAULT_KILL_EVENTS_COLLECTION)
    playlists_collection = st.text_input("Playlists collection", value=DEFAULT_PLAYLISTS_COLLECTION)
    refresh = st.button("Refresh data")

if refresh:
    load_runs_df.clear()
    load_kill_events_df.clear()
    load_playlists_df.clear()

try:
    runs_df = load_runs_df(mongo_uri, db_name, runs_collection)
except Exception as e:
    st.error(f"Could not load data from MongoDB: {e}")
    st.stop()

if runs_df.empty:
    st.warning("No run data found yet. Run the ingester first.")
    st.stop()

playlists_df = load_playlists_df(mongo_uri, db_name, playlists_collection)
playlist_lookup = build_playlist_lookup(playlists_df)
playlist_order_lookup = build_playlist_order_lookup(playlists_df)
playlist_options = sorted(playlist_lookup.keys())

with st.sidebar:
    st.header("View Settings")

    browse_mode = st.selectbox(
        "Browse by",
        ["Playlist", "Scenario", "Custom Group"],
        index=0,
    )

    custom_group_text = st.text_area(
        "Custom groups",
        value=(
        "Tracking:\n"
        "Kindaclose Long Strafes\n"
        "Kindaclose Fast Strafes Thin\n"
        ),
        height=180
    )

    st.caption("Format: group name followed by scenarios on separate lines.")

    custom_group_lookup = build_custom_group_lookup(custom_group_text)
    custom_group_order_lookup = build_custom_group_order_lookup(custom_group_text)

    runs_df["custom_group"] = runs_df["scenario_name"].map(custom_group_lookup)

    min_runs = st.number_input("Minimum runs per task", min_value=1, value=1, step=1)

filtered_df = runs_df.copy()

if browse_mode == "Playlist":
    playlist_choices = sorted(playlist_lookup.keys())

    if not playlist_choices:
        st.warning("No saved playlists were found in the playlists collection.")
        st.stop()

    selected_playlist = st.selectbox("Playlist", options=playlist_choices)
    playlist_scenarios = playlist_lookup.get(selected_playlist, set())
    playlist_order = playlist_order_lookup.get(selected_playlist, [])

    focus_df = filtered_df[
        filtered_df["scenario_name"].astype(str).str.strip().isin(playlist_scenarios)
    ].copy()

    section_title = selected_playlist

elif browse_mode == "Scenario":
    scenario_choices = sorted(
        runs_df["scenario_name"].dropna().astype(str).unique().tolist()
    )

    if not scenario_choices:
        st.warning("No scenarios found in run data.")
        st.stop()

    selected_scenario = st.selectbox("Scenario", options=scenario_choices)
    focus_df = filtered_df[filtered_df["scenario_name"] == selected_scenario].copy()
    section_title = selected_scenario

else:  # Custom Group
    custom_group_choices = list(custom_group_order_lookup.keys())

    if not custom_group_choices:
        st.warning("No custom groups found. Add some scenarios in the Custom groups box.")
        st.stop()

    selected_custom_group = st.selectbox("Custom Group", options=custom_group_choices)
    custom_group_order = custom_group_order_lookup.get(selected_custom_group, [])

    focus_df = filtered_df[filtered_df["custom_group"] == selected_custom_group].copy()
    section_title = selected_custom_group

if focus_df.empty:
    st.warning("No runs match the current selection.")
    st.stop()

if browse_mode == "Playlist":
    focus_task_summary = summarize_tasks(focus_df, scenario_order=playlist_order)
elif browse_mode == "Custom Group":
    focus_task_summary = summarize_tasks(focus_df, scenario_order=custom_group_order)
else:
    focus_task_summary = summarize_tasks(focus_df)

focus_task_summary = focus_task_summary[focus_task_summary["runs"] >= min_runs]

if focus_task_summary.empty:
    st.warning("No tasks found with the current minimum-run filter.")
    st.stop()

st.subheader(f"Tasks: {section_title}")
st.dataframe(
    focus_task_summary[["scenario_name", "runs", "best_score", "latest_run"]],
    use_container_width=True,
    hide_index=True,
)

st.subheader("Performance Trend")
task_options = focus_task_summary["scenario_name"].tolist()
selected_task = st.selectbox("Scenario", task_options)

task_df = focus_df[focus_df["scenario_name"] == selected_task].copy()

metric_choice = st.selectbox(
    "Metric",
    ["score", "accuracy_pct", "avg_ttk", "kills"],
    index=0,
)

render_performance_chart(task_df, metric_col=metric_choice)

st.subheader("Run Inspector")
run_picker_df = task_df[["_id", "scenario_name", "source_file", "challenge_start", "score"]].copy()
run_picker_df["label"] = run_picker_df.apply(
    lambda row: f"{row['scenario_name']} | {row.get('challenge_start', 'Unknown Time')} | score={row.get('score', 'N/A')}",
    axis=1,
)

if run_picker_df.empty:
    st.info("No runs available for the selected scenario.")
else:
    selected_run_label = st.selectbox("Choose a run", options=run_picker_df["label"].tolist())
    selected_run_id = run_picker_df.loc[run_picker_df["label"] == selected_run_label, "_id"].iloc[0]
    selected_run = task_df[task_df["_id"] == selected_run_id].iloc[0].to_dict()

    with st.expander("Run document"):
        st.json(selected_run)

    kill_df = load_kill_events_df(mongo_uri, db_name, kill_events_collection, [selected_run_id])
    if kill_df.empty:
        st.info("No kill events found for this run.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Kill Events", len(kill_df))
        with c2:
            st.metric(
                "Event Avg TTK",
                f"{kill_df['ttk_seconds'].mean():.3f}s"
                if kill_df["ttk_seconds"].notna().any()
                else "N/A",
            )
        with c3:
            event_acc = kill_df["accuracy"].mean() * 100 if kill_df["accuracy"].notna().any() else None
            st.metric("Event Avg Accuracy", f"{event_acc:.1f}%" if event_acc is not None else "N/A")

        col_a, col_b = st.columns(2)
        with col_a:
            ttk_chart = kill_df[["kill_number", "ttk_seconds"]].dropna().sort_values("kill_number")
            if not ttk_chart.empty:
                st.caption("TTK by kill")
                st.line_chart(ttk_chart.set_index("kill_number")[["ttk_seconds"]])

        with col_b:
            acc_chart = kill_df[["kill_number", "accuracy"]].dropna().sort_values("kill_number")
            if not acc_chart.empty:
                st.caption("Accuracy by kill")
                st.line_chart(acc_chart.set_index("kill_number")[["accuracy"]])

        st.dataframe(kill_df.sort_values("kill_number"), use_container_width=True, hide_index=True)

st.subheader("Run History")
history_df = task_df.copy().sort_values("challenge_start_dt", ascending=False)

history_cols = [
    col
    for col in [
        "scenario_name",
        "challenge_start",
        "score",
        "kills",
        "accuracy_pct",
        "avg_ttk",
        "source_file",
        "_id",
    ]
    if col in history_df.columns
]

st.dataframe(
    history_df[history_cols],
    use_container_width=True,
    hide_index=True,
)

with st.expander("Parser Audit"):
    audit_df = focus_df.copy()
    audit_df["kill_match"] = audit_df["kills"].fillna(-1) == audit_df["kill_count_parsed"].fillna(-2)

    audit_cols = [
        col
        for col in [
            "scenario_name",
            "source_file",
            "score",
            "kills",
            "kill_count_parsed",
            "hit_count",
            "miss_count",
            "accuracy_pct",
            "kill_match",
        ]
        if col in audit_df.columns
    ]

    st.dataframe(
        audit_df[audit_cols].sort_values(["kill_match", "score"], ascending=[True, False]),
        use_container_width=True,
        hide_index=True,
    )