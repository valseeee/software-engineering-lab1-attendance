from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path
from typing import Iterable, Optional


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = BASE_DIR / "instance" / "lemon.db"
DEFAULT_CSV_CANDIDATES = [
    Path("/data/attendance.csv"),
    BASE_DIR / "attendance.csv",
]


def resolve_csv_path(raw_path: Optional[str]) -> Path:
    if raw_path:
        csv_path = Path(raw_path).expanduser().resolve()
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")
        return csv_path

    for candidate in DEFAULT_CSV_CANDIDATES:
        if candidate.exists():
            return candidate.resolve()

    raise FileNotFoundError(
        "CSV file not found. Checked: "
        + ", ".join(str(path) for path in DEFAULT_CSV_CANDIDATES)
    )


def connect_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_required_tables(conn: sqlite3.Connection) -> None:
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('employee', 'attendance')"
    )
    tables = {row["name"] for row in cursor.fetchall()}
    missing = [name for name in ("employee", "attendance") if name not in tables]
    if missing:
        raise RuntimeError(f"Missing required table(s): {', '.join(missing)}")


def get_employee_ids(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.execute("SELECT employee_id FROM employee")
    return {str(row["employee_id"]).strip() for row in cursor.fetchall() if row["employee_id"] is not None}


def get_employee_ids_in_order(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute("SELECT employee_id FROM employee ORDER BY employee_id")
    return [str(row["employee_id"]).strip() for row in cursor.fetchall() if row["employee_id"] is not None]


def resolve_attendance_target_column(conn: sqlite3.Connection) -> str:
    cursor = conn.execute("PRAGMA table_info(attendance)")
    columns = {row[1] for row in cursor.fetchall()}
    if "attendance_status" in columns:
        return "attendance_status"
    if "status" in columns:
        return "status"
    raise RuntimeError(
        "attendance table does not contain 'attendance_status' or 'status' column"
    )


def load_rows(csv_path: Path) -> Iterable[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {"employee_id", "timestamp", "type", "status"}
        missing = expected - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing required column(s): {', '.join(sorted(missing))}")
        for row in reader:
            yield {
                "employee_id": (row.get("employee_id") or "").strip(),
                "timestamp": (row.get("timestamp") or "").strip(),
                "type": (row.get("type") or "").strip(),
                "status": (row.get("status") or "").strip(),
            }


def build_source_employee_mapping(csv_path: Path, target_employee_ids: list[str]) -> dict[str, str]:
    source_employee_ids: list[str] = []
    seen = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if "employee_id" not in (reader.fieldnames or []):
            raise ValueError("CSV missing required column: employee_id")
        for row in reader:
            employee_id = (row.get("employee_id") or "").strip()
            if employee_id and employee_id not in seen:
                source_employee_ids.append(employee_id)
                seen.add(employee_id)

    if not source_employee_ids:
        raise ValueError("CSV contains no employee_id values")
    if len(source_employee_ids) != len(target_employee_ids):
        raise RuntimeError(
            "Cannot auto-remap CSV employee_id values: "
            f"source has {len(source_employee_ids)} unique ids, "
            f"database has {len(target_employee_ids)} employee ids"
        )

    return dict(zip(source_employee_ids, target_employee_ids))


def import_attendance(csv_path: Path, db_path: Path, remap_to_current: bool) -> None:
    conn = connect_db(db_path)
    try:
        ensure_required_tables(conn)
        employee_ids = get_employee_ids(conn)
        ordered_employee_ids = get_employee_ids_in_order(conn)
        target_column = resolve_attendance_target_column(conn)
        source_mapping = (
            build_source_employee_mapping(csv_path, ordered_employee_ids)
            if remap_to_current
            else {}
        )

        if remap_to_current:
            print("Remap mode enabled")
            for source_id, target_id in source_mapping.items():
                print(f"  {source_id} -> {target_id}")

        inserted = 0
        skipped = 0

        insert_sql = (
            f"INSERT INTO attendance (employee_id, timestamp, type, {target_column}) "
            "VALUES (?, ?, ?, ?)"
        )

        conn.execute("BEGIN")
        try:
            for index, row in enumerate(load_rows(csv_path), start=1):
                source_employee_id = row["employee_id"]
                employee_id = source_mapping.get(source_employee_id, source_employee_id)
                timestamp = row["timestamp"]
                attendance_type = row["type"]
                status = row["status"]

                if not employee_id:
                    skipped += 1
                    print(f"[SKIP] row {index}: empty employee_id")
                    continue
                if employee_id not in employee_ids:
                    skipped += 1
                    print(
                        f"[SKIP] row {index}: employee_id {employee_id} "
                        f"not found in employee table (source={source_employee_id})"
                    )
                    continue
                if not timestamp or not attendance_type:
                    skipped += 1
                    print(
                        f"[SKIP] row {index}: missing timestamp or type "
                        f"(employee_id={employee_id}, source={source_employee_id})"
                    )
                    continue

                try:
                    conn.execute(
                        insert_sql,
                        (employee_id, timestamp, attendance_type, status),
                    )
                except sqlite3.IntegrityError as exc:
                    skipped += 1
                    print(f"[SKIP] row {index}: insert failed for employee_id {employee_id} ({exc})")
                    continue

                inserted += 1
                print(
                    f"[OK] row {index}: employee_id={employee_id}, source={source_employee_id}, "
                    f"timestamp={timestamp}, type={attendance_type}, status={status}"
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        print("\nImport summary")
        print(f"Inserted: {inserted}")
        print(f"Skipped: {skipped}")
        print(f"Database: {db_path}")
        print(f"CSV: {csv_path}")
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import attendance CSV data into existing SQLite database."
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        help="Path to attendance CSV file. Defaults to /data/attendance.csv or project root attendance.csv.",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=str(DEFAULT_DB_PATH),
        help="Path to SQLite database file. Default: instance/lemon.db",
    )
    parser.add_argument(
        "--remap-current",
        action="store_true",
        help="Remap CSV employee_id values to the current employee table ids in order.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    csv_path = resolve_csv_path(args.csv_path)
    db_path = Path(args.db_path).expanduser().resolve()
    import_attendance(csv_path, db_path, args.remap_current)


if __name__ == "__main__":
    main()
