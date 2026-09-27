import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from db.models import Base

# Automatically load .env file from project root if present
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(ROOT_DIR, ".env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'").strip('"')
            if k and k not in os.environ:
                os.environ[k] = v

DEFAULT_DB_PATH = os.path.join(ROOT_DIR, "data", "business_operator.db")
os.makedirs(os.path.dirname(DEFAULT_DB_PATH), exist_ok=True)
SQLITE_FALLBACK_URL = f"sqlite:///{DEFAULT_DB_PATH}"

raw_db_url = os.environ.get("DATABASE_URL", "").strip()

def _build_working_engine():
    if raw_db_url and not raw_db_url.startswith("sqlite"):
        try:
            pg_engine = create_engine(
                raw_db_url,
                echo=False,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 10}
            )
            with pg_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("[Database] Connected to live Supabase PostgreSQL database!")
            return pg_engine, raw_db_url
        except Exception as e:
            print(f"[Database] Note: Remote PostgreSQL unreachable ({e.__class__.__name__}). Using persistent local SQLite database ({DEFAULT_DB_PATH}).")

    sqlite_engine = create_engine(
        SQLITE_FALLBACK_URL,
        echo=False,
        connect_args={"check_same_thread": False}
    )
    return sqlite_engine, SQLITE_FALLBACK_URL

engine, DATABASE_URL = _build_working_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
try:
    Base.metadata.create_all(bind=engine)
except Exception as _db_init_err:
    print(f"[Database] Table creation check warning: {_db_init_err}")

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
