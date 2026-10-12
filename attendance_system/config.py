import os
from pathlib import Path
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    DB_BACKEND = os.getenv("DB_BACKEND", "sqlite").lower()
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_USER = os.getenv("MYSQL_USER", "root")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE") or os.getenv("MYSQL_DB", "lemon")
    SQLITE_URI = f"sqlite:///{INSTANCE_DIR / 'lemon.db'}"
    MYSQL_URI = (
        f"mysql+pymysql://{quote_plus(MYSQL_USER)}:{quote_plus(MYSQL_PASSWORD)}"
        f"@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}?charset=utf8mb4"
    )
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL") or (
        MYSQL_URI if DB_BACKEND == "mysql" else SQLITE_URI
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
    }
    UPLOAD_FOLDER = str(BASE_DIR / "uploads")
    FACE_TEMPLATE_FOLDER = str(BASE_DIR / "uploads" / "face_templates")
    AVATAR_MAX_SIZE = 2 * 1024 * 1024
    FACE_TEMPLATE_MAX_SIZE = 2 * 1024 * 1024
    FACE_CAPTURE_MAX_SIZE = 2 * 1024 * 1024
    FACE_RECOGNITION_MAX_DISTANCE = 70
