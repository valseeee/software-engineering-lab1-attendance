import argparse
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import BASE_DIR
from models import (
    Admin,
    Analysis,
    Attendance,
    Employee,
    EmployeeFaceProfile,
    LeaveRequest,
)


TABLE_MODELS = [
    Admin,
    Employee,
    Attendance,
    LeaveRequest,
    Analysis,
    EmployeeFaceProfile,
]


def build_sqlite_url(sqlite_path):
    return f"sqlite:///{Path(sqlite_path).resolve()}"


def get_default_sqlite_path():
    return BASE_DIR / "instance" / "lemon.db"


def get_mysql_url(args):
    if args.mysql_url:
        return args.mysql_url
    if os.getenv("DATABASE_URL", "").startswith("mysql"):
        return os.getenv("DATABASE_URL")

    host = os.getenv("MYSQL_HOST", "127.0.0.1")
    port = os.getenv("MYSQL_PORT", "3306")
    database = os.getenv("MYSQL_DATABASE") or os.getenv("MYSQL_DB", "lemon")
    user = os.getenv("MYSQL_USER", "root")
    password = os.getenv("MYSQL_PASSWORD", "123456")
    return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"


def create_missing_tables(target_engine):
    for model in TABLE_MODELS:
        model.__table__.create(bind=target_engine, checkfirst=True)


def row_exists(connection, table, primary_key_value):
    primary_key = list(table.primary_key.columns)[0]
    return connection.execute(
        select(primary_key).where(primary_key == primary_key_value)
    ).first() is not None


def migrate_table(source_connection, target_connection, model):
    table = model.__table__
    primary_key = list(table.primary_key.columns)[0]
    inserted = 0
    skipped = 0

    rows = source_connection.execute(select(table).order_by(primary_key.asc())).all()
    for row in rows:
        payload = dict(row._mapping)
        primary_key_value = payload.get(primary_key.name)
        if row_exists(target_connection, table, primary_key_value):
            skipped += 1
            continue

        target_connection.execute(table.insert().values(**payload))
        inserted += 1

    return inserted, skipped


def migrate(sqlite_path, mysql_url, create_tables_only=False):
    if not Path(sqlite_path).exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    source_engine = create_engine(build_sqlite_url(sqlite_path))
    target_engine = create_engine(mysql_url, pool_pre_ping=True)

    create_missing_tables(target_engine)
    if create_tables_only:
        print("MySQL table structure checked/created. No data was imported.")
        return

    with source_engine.connect() as source_connection:
        with target_engine.begin() as target_connection:
            for model in TABLE_MODELS:
                inserted, skipped = migrate_table(
                    source_connection,
                    target_connection,
                    model,
                )
                print(
                    f"{model.__tablename__}: inserted={inserted}, skipped={skipped}"
                )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Incrementally migrate Lemon SQLite data into MySQL.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(get_default_sqlite_path()),
        help="Path to the source SQLite database.",
    )
    parser.add_argument(
        "--mysql-url",
        default="",
        help="Target MySQL SQLAlchemy URL. Defaults to DATABASE_URL or MYSQL_* env.",
    )
    parser.add_argument(
        "--create-tables-only",
        action="store_true",
        help="Only create/check missing MySQL tables; do not import data.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    migrate(
        sqlite_path=arguments.sqlite_path,
        mysql_url=get_mysql_url(arguments),
        create_tables_only=arguments.create_tables_only,
    )
