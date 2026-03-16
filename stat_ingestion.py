from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from datetime import datetime


# =========================
# Configuration
# =========================

DEFAULT_KOVAAKS_ROOT = r"C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer"
DEFAULT_STATS_DIR = rf"{DEFAULT_KOVAAKS_ROOT}\stats"
DEFAULT_PLAYLISTS_DIR = rf"{DEFAULT_KOVAAKS_ROOT}\Saved\SaveGames\Playlists"

DEFAULT_DB_PATH = "kovaaks_tracker.db"


# =========================
# Helpers
# =========================

def safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def clean_string(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def parse_ttk(ttk_str: str | None) -> float | None:
    if ttk_str is None:
        return None
    ttk_str = ttk_str.strip().replace("s", "")
    return safe_float(ttk_str)


def compute_file_sha256(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def normalize_scenario_name(name: str | None) -> str | None:
    if not name:
        return None
    return str(name).split(" - ")[0].strip()


def parse_filename_metadata(file_path: Path) -> dict[str, Any]:
    stem = file_path.stem
    result = {
        "challenge_name_from_file": None,
        "file_timestamp_str": None,
    }

    parts = stem.split(" - ")

    if len(parts) >= 2:
        result["challenge_name_from_file"] = " - ".join(parts[:-1]).strip()
        result["file_timestamp_str"] = parts[-1].replace(" Stats", "").strip()
    else:
        result["challenge_name_from_file"] = stem.strip()

    return result


def parse_filename_timestamp(timestamp_str: str | None) -> str | None:
    if not timestamp_str:
        return None

    ts = timestamp_str.strip()

    formats = [
        "%Y.%m.%d-%H.%M.%S",
        "%Y-%m-%d %H-%M-%S",
        "%Y-%m-%d %H.%M.%S",
        "%Y.%m.%d %H.%M.%S",
        "%m-%d-%Y %H-%M-%S",
        "%m.%d.%Y-%H.%M.%S",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(ts, fmt)
            return dt.isoformat()
        except ValueError:
            continue

    return None


# =========================
# SQLite helpers
# =========================

def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        scenario_hash TEXT,
        file_sha256 TEXT,
        source_file TEXT,
        source_path TEXT,
        scenario_name TEXT,
        challenge_name TEXT,
        score REAL,
        kills INTEGER,
        deaths INTEGER,
        fight_time REAL,
        time_remaining REAL,
        avg_ttk REAL,
        damage_done REAL,
        hit_count INTEGER,
        miss_count INTEGER,
        pause_count INTEGER,
        pause_duration REAL,
        avg_target_scale REAL,
        avg_time_dilation REAL,
        input_lag REAL,
        max_fps_config REAL,
        sens_scale TEXT,
        challenge_start TEXT,
        run_datetime TEXT,
        file_timestamp_str TEXT,
        game_version TEXT,
        kill_count_parsed INTEGER,
        raw_summary_json TEXT,
        file_metadata_json TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS kill_events (
        run_id TEXT NOT NULL,
        kill_number INTEGER NOT NULL,
        timestamp TEXT,
        bot TEXT,
        weapon TEXT,
        ttk_seconds REAL,
        shots INTEGER,
        hits INTEGER,
        accuracy REAL,
        damage_done REAL,
        damage_possible REAL,
        efficiency REAL,
        cheated INTEGER,
        overshots INTEGER,
        scenario_name TEXT,
        source_file TEXT,
        PRIMARY KEY (run_id, kill_number)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS playlists (
        playlist_id TEXT PRIMARY KEY,
        playlist_name TEXT,
        author_steam_id TEXT,
        author_name TEXT,
        description TEXT,
        scenario_count INTEGER,
        source_file TEXT,
        source_path TEXT,
        has_offline_scenarios INTEGER,
        has_edited INTEGER,
        share_code TEXT,
        version TEXT,
        updated TEXT,
        is_private INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS playlist_scenarios (
        playlist_id TEXT NOT NULL,
        scenario_name TEXT NOT NULL,
        PRIMARY KEY (playlist_id, scenario_name)
    )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_scenario_name ON runs (scenario_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_challenge_start ON runs (challenge_start)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_score ON runs (score)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_source_file ON runs (source_file)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_scenario_hash ON runs (scenario_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kill_events_run_id ON kill_events (run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kill_events_scenario_name ON kill_events (scenario_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_playlists_name ON playlists (playlist_name)")

    conn.commit()


# =========================
# Stat file parsing
# =========================

def parse_kill_events(lines: list[str]) -> list[dict[str, Any]]:
    kill_header = "Kill #,Timestamp,Bot,Weapon,TTK"
    start_idx = None

    for i, line in enumerate(lines):
        if line.startswith(kill_header):
            start_idx = i
            break

    if start_idx is None:
        return []

    csv_lines = [lines[start_idx]]

    for line in lines[start_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Weapon,Shots,Hits,Damage Done,Damage Possible"):
            break
        csv_lines.append(line)

    reader = csv.DictReader(csv_lines)
    events: list[dict[str, Any]] = []

    for row in reader:
        events.append({
            "kill_number": safe_int(row.get("Kill #")),
            "timestamp": clean_string(row.get("Timestamp")),
            "bot": clean_string(row.get("Bot")),
            "weapon": clean_string(row.get("Weapon")),
            "ttk_seconds": parse_ttk(row.get("TTK")),
            "shots": safe_int(row.get("Shots")),
            "hits": safe_int(row.get("Hits")),
            "accuracy": safe_float(row.get("Accuracy")),
            "damage_done": safe_float(row.get("Damage Done")),
            "damage_possible": safe_float(row.get("Damage Possible")),
            "efficiency": safe_float(row.get("Efficiency")),
            "cheated": safe_int(row.get("Cheated")),
            "overshots": safe_int(row.get("OverShots")),
        })

    return events


def parse_weapon_summary(lines: list[str]) -> list[dict[str, Any]]:
    weapon_header = "Weapon,Shots,Hits,Damage Done,Damage Possible"
    start_idx = None

    for i, line in enumerate(lines):
        if line.startswith(weapon_header):
            start_idx = i
            break

    if start_idx is None:
        return []

    csv_lines = [lines[start_idx]]

    for line in lines[start_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Kills:,"):
            break
        csv_lines.append(line)

    reader = csv.DictReader(csv_lines)
    weapons: list[dict[str, Any]] = []

    for row in reader:
        weapons.append({
            "weapon": clean_string(row.get("Weapon")),
            "shots": safe_int(row.get("Shots")),
            "hits": safe_int(row.get("Hits")),
            "damage_done": safe_float(row.get("Damage Done")),
            "damage_possible": safe_float(row.get("Damage Possible")),
            "efficiency": safe_float(row.get("Efficiency")),
        })

    return weapons


def parse_summary(lines: list[str]) -> dict[str, str]:
    summary_start = None

    for i, line in enumerate(lines):
        if line.startswith("Kills:,"):
            summary_start = i
            break

    if summary_start is None:
        return {}

    summary: dict[str, str] = {}

    for line in lines[summary_start:]:
        stripped = line.strip()
        if not stripped or "," not in stripped:
            continue

        key, value = stripped.split(",", 1)
        summary[key.strip().rstrip(":")] = value.strip()

    return summary


def parse_kovaaks_stats_file(file_path: Path) -> dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n").rstrip("\r") for line in f]

    file_sha256 = compute_file_sha256(file_path)
    filename_meta = parse_filename_metadata(file_path)
    file_timestamp_iso = parse_filename_timestamp(filename_meta.get("file_timestamp_str"))

    kill_events = parse_kill_events(lines)
    weapon_summary = parse_weapon_summary(lines)
    summary = parse_summary(lines)

    scenario_name = normalize_scenario_name(summary.get("Scenario"))
    challenge_name = clean_string(filename_meta.get("challenge_name_from_file"))
    scenario_hash = clean_string(summary.get("Hash"))
    run_id = file_sha256

    run_doc = {
        "run_id": run_id,
        "scenario_hash": scenario_hash,
        "file_sha256": file_sha256,
        "source_file": file_path.name,
        "source_path": str(file_path),
        "scenario_name": scenario_name,
        "challenge_name": challenge_name,
        "score": safe_float(summary.get("Score")),
        "kills": safe_int(summary.get("Kills")),
        "deaths": safe_int(summary.get("Deaths")),
        "fight_time": safe_float(summary.get("Fight Time")),
        "time_remaining": safe_float(summary.get("Time Remaining")),
        "avg_ttk": safe_float(summary.get("Avg TTK")),
        "damage_done": safe_float(summary.get("Damage Done")),
        "hit_count": safe_int(summary.get("Hit Count")),
        "miss_count": safe_int(summary.get("Miss Count")),
        "pause_count": safe_int(summary.get("Pause Count")),
        "pause_duration": safe_float(summary.get("Pause Duration")),
        "avg_target_scale": safe_float(summary.get("Avg Target Scale")),
        "avg_time_dilation": safe_float(summary.get("Avg Time Dilation")),
        "input_lag": safe_float(summary.get("Input Lag")),
        "max_fps_config": safe_float(summary.get("Max FPS (config)")),
        "sens_scale": clean_string(summary.get("Sens Scale")),
        "challenge_start": clean_string(summary.get("Challenge Start")),
        "run_datetime": file_timestamp_iso,
        "file_timestamp_str": filename_meta.get("file_timestamp_str"),
        "game_version": clean_string(summary.get("Game Version")),
        "kill_count_parsed": len(kill_events),
        "raw_summary_json": json.dumps(summary),
        "file_metadata_json": json.dumps(filename_meta),
        "weapon_summary_json": json.dumps(weapon_summary),
    }

    for event in kill_events:
        event["run_id"] = run_id
        event["scenario_name"] = scenario_name
        event["source_file"] = file_path.name

    return {
        "run": run_doc,
        "kill_events": kill_events,
    }


# =========================
# Playlist parsing
# =========================

def parse_playlist_file(file_path: Path) -> dict[str, Any]:
    raw = file_path.read_bytes()

    payload = None
    last_error = None

    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = raw.decode(encoding)
            payload = json.loads(text)
            break
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            last_error = e

    if payload is None:
        raise ValueError(f"Could not parse playlist file {file_path}: {last_error}")

    playlist_id = payload.get("playlistId")
    playlist_name = clean_string(payload.get("playlistName")) or file_path.stem

    scenario_list = payload.get("scenarioList") or []
    if not isinstance(scenario_list, list):
        scenario_list = []

    normalized_scenarios: list[str] = []
    for item in scenario_list:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("scenario_name")
        name = normalize_scenario_name(raw_name)
        if name:
            normalized_scenarios.append(name)

    playlist_doc = {
        "_id": str(playlist_id) if playlist_id is not None else compute_file_sha256(file_path),
        "playlist_id": playlist_id,
        "playlist_name": playlist_name,
        "author_steam_id": clean_string(payload.get("authorSteamId")),
        "author_name": clean_string(payload.get("authorName")),
        "description": clean_string(payload.get("description")),
        "scenario_names": normalized_scenarios,
        "scenario_count": len(normalized_scenarios),
        "source_file": file_path.name,
        "source_path": str(file_path),
        "has_offline_scenarios": payload.get("hasOfflineScenarios"),
        "has_edited": payload.get("hasEdited"),
        "share_code": clean_string(payload.get("shareCode")),
        "version": payload.get("version"),
        "updated": payload.get("updated"),
        "is_private": payload.get("isPrivate"),
    }

    return playlist_doc


# =========================
# SQLite write helpers
# =========================

def upsert_run_and_events(conn: sqlite3.Connection, parsed: dict[str, Any]) -> tuple[bool, int]:
    run_doc = parsed["run"]
    events = parsed["kill_events"]
    run_id = run_doc["run_id"]

    existing = conn.execute(
        "SELECT 1 FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()

    conn.execute("""
        INSERT OR REPLACE INTO runs (
            run_id, scenario_hash, file_sha256, source_file, source_path,
            scenario_name, challenge_name, score, kills, deaths, fight_time,
            time_remaining, avg_ttk, damage_done, hit_count, miss_count,
            pause_count, pause_duration, avg_target_scale, avg_time_dilation,
            input_lag, max_fps_config, sens_scale, challenge_start, run_datetime,
            file_timestamp_str, game_version, kill_count_parsed,
            raw_summary_json, file_metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_doc["run_id"],
        run_doc["scenario_hash"],
        run_doc["file_sha256"],
        run_doc["source_file"],
        run_doc["source_path"],
        run_doc["scenario_name"],
        run_doc["challenge_name"],
        run_doc["score"],
        run_doc["kills"],
        run_doc["deaths"],
        run_doc["fight_time"],
        run_doc["time_remaining"],
        run_doc["avg_ttk"],
        run_doc["damage_done"],
        run_doc["hit_count"],
        run_doc["miss_count"],
        run_doc["pause_count"],
        run_doc["pause_duration"],
        run_doc["avg_target_scale"],
        run_doc["avg_time_dilation"],
        run_doc["input_lag"],
        run_doc["max_fps_config"],
        run_doc["sens_scale"],
        run_doc["challenge_start"],
        run_doc["run_datetime"],
        run_doc["file_timestamp_str"],
        run_doc["game_version"],
        run_doc["kill_count_parsed"],
        run_doc["raw_summary_json"],
        run_doc["file_metadata_json"],
    ))

    conn.execute("DELETE FROM kill_events WHERE run_id = ?", (run_id,))

    if events:
        conn.executemany("""
            INSERT OR REPLACE INTO kill_events (
                run_id, kill_number, timestamp, bot, weapon, ttk_seconds,
                shots, hits, accuracy, damage_done, damage_possible,
                efficiency, cheated, overshots, scenario_name, source_file
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (
                event["run_id"],
                event["kill_number"],
                event["timestamp"],
                event["bot"],
                event["weapon"],
                event["ttk_seconds"],
                event["shots"],
                event["hits"],
                event["accuracy"],
                event["damage_done"],
                event["damage_possible"],
                event["efficiency"],
                event["cheated"],
                event["overshots"],
                event["scenario_name"],
                event["source_file"],
            )
            for event in events
        ])

    conn.commit()
    return existing is None, len(events)


def upsert_playlist(conn: sqlite3.Connection, playlist_doc: dict[str, Any]) -> bool:
    playlist_id = str(playlist_doc["playlist_id"]) if playlist_doc["playlist_id"] is not None else playlist_doc["_id"]

    existing = conn.execute(
        "SELECT 1 FROM playlists WHERE playlist_id = ?",
        (playlist_id,),
    ).fetchone()

    conn.execute("""
        INSERT OR REPLACE INTO playlists (
            playlist_id, playlist_name, author_steam_id, author_name,
            description, scenario_count, source_file, source_path,
            has_offline_scenarios, has_edited, share_code, version,
            updated, is_private
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        playlist_id,
        playlist_doc["playlist_name"],
        playlist_doc["author_steam_id"],
        playlist_doc["author_name"],
        playlist_doc["description"],
        playlist_doc["scenario_count"],
        playlist_doc["source_file"],
        playlist_doc["source_path"],
        playlist_doc["has_offline_scenarios"],
        playlist_doc["has_edited"],
        playlist_doc["share_code"],
        str(playlist_doc["version"]) if playlist_doc["version"] is not None else None,
        str(playlist_doc["updated"]) if playlist_doc["updated"] is not None else None,
        playlist_doc["is_private"],
    ))

    conn.execute("DELETE FROM playlist_scenarios WHERE playlist_id = ?", (playlist_id,))

    scenario_names = playlist_doc.get("scenario_names", [])
    if scenario_names:
        conn.executemany("""
            INSERT OR REPLACE INTO playlist_scenarios (playlist_id, scenario_name)
            VALUES (?, ?)
        """, [(playlist_id, s) for s in scenario_names])

    conn.commit()
    return existing is None


# =========================
# Ingestion entrypoints
# =========================

def ingest_stats_folder(
    conn: sqlite3.Connection,
    stats_dir: str | Path = DEFAULT_STATS_DIR,
) -> dict[str, Any]:
    stats_path = Path(stats_dir)
    if not stats_path.exists():
        raise FileNotFoundError(f"Stats folder not found: {stats_path}")

    csv_files = sorted(stats_path.rglob("*.csv"))

    results = {
        "files_found": len(csv_files),
        "files_processed": 0,
        "runs_inserted_or_new": 0,
        "kill_events_written": 0,
        "errors": [],
    }

    for file_path in csv_files:
        try:
            parsed = parse_kovaaks_stats_file(file_path)
            inserted, event_count = upsert_run_and_events(conn, parsed)

            results["files_processed"] += 1
            results["kill_events_written"] += event_count
            if inserted:
                results["runs_inserted_or_new"] += 1

            print(f"[STATS OK] {file_path.name}")
        except Exception as e:
            results["errors"].append({"file": str(file_path), "error": str(e)})
            print(f"[STATS ERROR] {file_path.name}: {e}")

    return results


def ingest_playlists_folder(
    conn: sqlite3.Connection,
    playlists_dir: str | Path = DEFAULT_PLAYLISTS_DIR,
) -> dict[str, Any]:
    playlists_path = Path(playlists_dir)
    if not playlists_path.exists():
        raise FileNotFoundError(f"Playlists folder not found: {playlists_path}")

    json_files = sorted(playlists_path.rglob("*.json"))

    results = {
        "files_found": len(json_files),
        "files_processed": 0,
        "playlists_inserted_or_new": 0,
        "errors": [],
    }

    for file_path in json_files:
        try:
            playlist_doc = parse_playlist_file(file_path)
            inserted = upsert_playlist(conn, playlist_doc)

            results["files_processed"] += 1
            if inserted:
                results["playlists_inserted_or_new"] += 1

            print(f"[PLAYLIST OK] {file_path.name}")
        except Exception as e:
            results["errors"].append({"file": str(file_path), "error": str(e)})
            print(f"[PLAYLIST ERROR] {file_path.name}: {e}")

    return results


def ingest_all(
    stats_dir: str | Path = DEFAULT_STATS_DIR,
    playlists_dir: str | Path = DEFAULT_PLAYLISTS_DIR,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    conn = get_connection(db_path=db_path)
    ensure_schema(conn)

    stats_summary = ingest_stats_folder(conn, stats_dir=stats_dir)
    playlists_summary = ingest_playlists_folder(conn, playlists_dir=playlists_dir)

    conn.close()

    return {
        "stats": stats_summary,
        "playlists": playlists_summary,
    }


if __name__ == "__main__":
    stats_dir = os.getenv("KOVAAKS_STATS_DIR", DEFAULT_STATS_DIR)
    playlists_dir = os.getenv("KOVAAKS_PLAYLISTS_DIR", DEFAULT_PLAYLISTS_DIR)
    db_path = os.getenv("KOVAAKS_DB_PATH", DEFAULT_DB_PATH)

    summary = ingest_all(
        stats_dir=stats_dir,
        playlists_dir=playlists_dir,
        db_path=db_path,
    )

    print("\n=== Stats Summary ===")
    print(summary["stats"])
    print("\n=== Playlist Summary ===")
    print(summary["playlists"])