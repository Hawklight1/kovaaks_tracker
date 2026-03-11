from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from datetime import datetime

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database


# =========================
# Configuration
# =========================

DEFAULT_KOVAAKS_ROOT = r"C:\Program Files (x86)\Steam\steamapps\common\FPSAimTrainer\FPSAimTrainer"
DEFAULT_STATS_DIR = rf"{DEFAULT_KOVAAKS_ROOT}\stats"
DEFAULT_PLAYLISTS_DIR = rf"{DEFAULT_KOVAAKS_ROOT}\Saved\SaveGames\Playlists"

DEFAULT_MONGO_URI = "mongodb://localhost:27017/"
DEFAULT_DB_NAME = "kovaaks_tracker"


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

    # Try several common formats Kovaak's filenames might use
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
        "_id": run_id,
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
        "weapon_summary": weapon_summary,
        "kill_count_parsed": len(kill_events),
        "raw_summary": summary,
        "file_metadata": filename_meta,
        "playlist_names": [],
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
# Mongo helpers
# =========================

def get_database(mongo_uri: str = DEFAULT_MONGO_URI, db_name: str = DEFAULT_DB_NAME) -> Database:
    client = MongoClient(mongo_uri)
    return client[db_name]


def ensure_indexes(db: Database) -> None:
    db.runs.create_index("scenario_name")
    db.runs.create_index("challenge_start")
    db.runs.create_index("score")
    db.runs.create_index("source_file")
    db.runs.create_index("file_sha256", unique=True)
    db.runs.create_index("scenario_hash")
    db.runs.create_index("playlist_names")

    db.kill_events.create_index("run_id")
    db.kill_events.create_index([("run_id", 1), ("kill_number", 1)], unique=True)
    db.kill_events.create_index("scenario_name")

    db.playlists.create_index("playlist_name", unique=True)
    db.playlists.create_index("scenario_names")


def upsert_run_and_events(db: Database, parsed: dict[str, Any]) -> tuple[bool, int]:
    runs: Collection = db.runs
    kill_events: Collection = db.kill_events

    run_doc = parsed["run"]
    events = parsed["kill_events"]
    run_id = run_doc["_id"]

    existing = runs.find_one({"_id": run_id}, {"_id": 1})

    runs.update_one({"_id": run_id}, {"$set": run_doc}, upsert=True)

    kill_events.delete_many({"run_id": run_id})

    if events:
        ops = [
            UpdateOne(
                {"run_id": event["run_id"], "kill_number": event["kill_number"]},
                {"$set": event},
                upsert=True,
            )
            for event in events
        ]
        kill_events.bulk_write(ops, ordered=False)

    return existing is None, len(events)


def upsert_playlist(db: Database, playlist_doc: dict[str, Any]) -> bool:
    playlists: Collection = db.playlists
    existing = playlists.find_one({"_id": playlist_doc["_id"]}, {"_id": 1})
    playlists.update_one({"_id": playlist_doc["_id"]}, {"$set": playlist_doc}, upsert=True)
    return existing is None


def link_runs_to_playlists(db: Database) -> None:
    """
    Populate runs.playlist_names based on scenario_name membership in ingested playlists.
    """
    playlists = list(db.playlists.find({}, {"playlist_name": 1, "scenario_names": 1}))
    scenario_to_playlists: dict[str, list[str]] = {}

    for playlist in playlists:
        playlist_name = playlist.get("playlist_name")
        for scenario_name in playlist.get("scenario_names", []):
            scenario_to_playlists.setdefault(scenario_name, []).append(playlist_name)

    for scenario_name, playlist_names in scenario_to_playlists.items():
        db.runs.update_many(
            {"scenario_name": scenario_name},
            {"$set": {"playlist_names": sorted(set(playlist_names))}},
        )

    db.runs.update_many(
        {"scenario_name": {"$nin": list(scenario_to_playlists.keys())}},
        {"$set": {"playlist_names": []}},
    )


# =========================
# Ingestion entrypoints
# =========================

def ingest_stats_folder(
    db: Database,
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
            inserted, event_count = upsert_run_and_events(db, parsed)

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
    db: Database,
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
            inserted = upsert_playlist(db, playlist_doc)

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
    mongo_uri: str = DEFAULT_MONGO_URI,
    db_name: str = DEFAULT_DB_NAME,
) -> dict[str, Any]:
    db = get_database(mongo_uri=mongo_uri, db_name=db_name)
    ensure_indexes(db)

    stats_summary = ingest_stats_folder(db, stats_dir=stats_dir)
    playlists_summary = ingest_playlists_folder(db, playlists_dir=playlists_dir)
    link_runs_to_playlists(db)

    return {
        "stats": stats_summary,
        "playlists": playlists_summary,
    }


if __name__ == "__main__":
    stats_dir = os.getenv("KOVAAKS_STATS_DIR", DEFAULT_STATS_DIR)
    playlists_dir = os.getenv("KOVAAKS_PLAYLISTS_DIR", DEFAULT_PLAYLISTS_DIR)
    mongo_uri = os.getenv("MONGO_URI", DEFAULT_MONGO_URI)
    db_name = os.getenv("MONGO_DB_NAME", DEFAULT_DB_NAME)

    summary = ingest_all(
        stats_dir=stats_dir,
        playlists_dir=playlists_dir,
        mongo_uri=mongo_uri,
        db_name=db_name,
    )

    print("\n=== Stats Summary ===")
    print(summary["stats"])
    print("\n=== Playlist Summary ===")
    print(summary["playlists"])