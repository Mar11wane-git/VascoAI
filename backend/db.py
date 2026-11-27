"""Storage for Vasco: users, sessions, profiles, preferences, usage stats and activity.

MySQL locally (database "Vasco"); PostgreSQL when a postgres:// DATABASE_URL is set
(e.g. the free Neon database Vercel provisions). Queries are written for MySQL and
translated for PostgreSQL by `_pg_sql`."""
import contextlib
import json
import os
import queue
import re
import ssl
import threading
import time
import urllib.parse
from datetime import date, datetime
from typing import Any, Dict, Iterator, List, Optional

import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # only needed with a PostgreSQL DATABASE_URL
    psycopg = None

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
LEGACY_JSON_DB = os.path.join(os.path.dirname(__file__), "users_db.json")
load_dotenv(ENV_PATH)


class DatabaseError(RuntimeError):
    pass


DRIVER_ERRORS: tuple = (pymysql.MySQLError,) + ((psycopg.Error,) if psycopg else ())


def _pg_url() -> str:
    """A PostgreSQL URL from DATABASE_URL / POSTGRES_URL, unless MySQL is configured explicitly."""
    if (os.getenv("MYSQL_URL") or os.getenv("MYSQL_HOST") or "").strip():
        return ""
    for name in ("DATABASE_URL", "POSTGRES_URL"):
        url = (os.getenv(name) or "").strip()
        if url.startswith(("postgres://", "postgresql://")):
            return url
    return ""


def is_postgres() -> bool:
    return bool(_pg_url())


def describe() -> str:
    """Where the data lives, without credentials (for logs)."""
    if is_postgres():
        u = urllib.parse.urlparse(_pg_url())
        return f"PostgreSQL {u.hostname}{u.path}"
    cfg = _config()
    return f"MySQL {cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}"


def _pg_conninfo() -> str:
    """Hosted PostgreSQL URLs ask for sslmode=require, which encrypts without checking the
    server certificate: upgrade it to full verification against the public CA bundle."""
    url = _pg_url()
    u = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(u.query)
    mode = (qs.get("sslmode") or [""])[-1]
    if u.hostname not in ("localhost", "127.0.0.1", "::1") and mode in ("", "prefer", "require"):
        import certifi
        qs["sslmode"] = ["verify-full"]
        qs.setdefault("sslrootcert", [certifi.where()])
        url = urllib.parse.urlunparse(u._replace(query=urllib.parse.urlencode(qs, doseq=True)))
    return url


_INTERVAL_RE = re.compile(r"INTERVAL\s+(%s|\d+)\s+(MINUTE|HOUR|DAY)\b", re.I)


def _pg_sql(sql: str) -> str:
    """Translates the MySQL dialect used in this app to PostgreSQL."""
    sql = sql.replace("`", '"')
    sql = _INTERVAL_RE.sub(lambda m: f"({m.group(1)} * INTERVAL '1 {m.group(2).lower()}')", sql)
    if re.match(r"\s*INSERT\s+IGNORE\s+INTO", sql, re.I):
        sql = re.sub(r"INSERT\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1, flags=re.I).rstrip()
        sql += " ON CONFLICT DO NOTHING"
    return sql


def _run(cur: Any, sql: str, params: Any = None) -> int:
    """Executes one statement on either driver; returns the affected row count."""
    if psycopg is not None and isinstance(cur, psycopg.Cursor):
        cur.execute(_pg_sql(sql), params)
        return max(cur.rowcount, 0)
    return cur.execute(sql, params)


def _config() -> Dict[str, Any]:
    # Hosted MySQL services (Railway, Aiven…) give a single URL: mysql://user:password@host:port/database
    ssl_mode = os.getenv("MYSQL_SSL_MODE", "").strip().upper()
    url = (os.getenv("MYSQL_URL") or "").strip()
    if url:
        u = urllib.parse.urlparse(url)
        qs = {k.lower(): v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        ssl_mode = ssl_mode or (qs.get("ssl-mode") or qs.get("sslmode") or "").upper()
        if not ssl_mode and qs.get("ssl", "").lower() in ("true", "1"):
            ssl_mode = "VERIFY_IDENTITY"
        return {
            "host": u.hostname or "127.0.0.1",
            "port": u.port or 3306,
            "user": urllib.parse.unquote(u.username or "root"),
            "password": urllib.parse.unquote(u.password or ""),
            "database": (u.path or "/").lstrip("/") or os.getenv("MYSQL_DATABASE", "Vasco").strip(),
            "ssl_mode": ssl_mode,
        }
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1").strip(),
        "port": int(os.getenv("MYSQL_PORT", "3306").strip() or 3306),
        "user": os.getenv("MYSQL_USER", "root").strip(),
        "password": os.getenv("MYSQL_PASSWORD", "1234"),
        "database": os.getenv("MYSQL_DATABASE", "Vasco").strip(),
        "ssl_mode": ssl_mode,
    }


def _ssl_context(mode: str) -> Optional[ssl.SSLContext]:
    """Encrypted connection for hosted MySQL (any ssl-mode other than DISABLED/PREFERRED).
    The server certificate is always verified: against MYSQL_SSL_CA (the provider's CA, PEM text)
    when given, otherwise against the system's trusted authorities."""
    if mode in ("", "DISABLED", "PREFERRED"):
        return None
    ca_pem = os.getenv("MYSQL_SSL_CA", "").strip().replace("\\n", "\n")
    ctx = ssl.create_default_context(cadata=ca_pem or None)
    if mode in ("REQUIRED", "VERIFY_CA") and ca_pem:
        # Provider CAs (e.g. Aiven) sign certificates whose names may not match the host alias.
        ctx.check_hostname = False
    return ctx


def _connect(with_db: bool = True) -> Any:
    if is_postgres():
        if psycopg is None:
            raise DatabaseError("DATABASE_URL PostgreSQL configurée mais le paquet psycopg n'est pas installé.")
        try:
            return psycopg.connect(_pg_conninfo(), row_factory=dict_row, autocommit=True, connect_timeout=8)
        except psycopg.Error as e:
            raise DatabaseError(f"Connexion PostgreSQL impossible ({describe()}) : {e}") from e
    cfg = _config()
    kwargs = dict(
        ssl=_ssl_context(cfg["ssl_mode"]),
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=8,
    )
    if with_db:
        kwargs["database"] = cfg["database"]
    try:
        return pymysql.connect(**kwargs)
    except pymysql.MySQLError as e:
        raise DatabaseError(
            f"Connexion MySQL impossible ({cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}) : {e}"
        ) from e


# Opening a connection costs ~300 ms (more with TLS): reuse them.
_pool: "queue.LifoQueue[Any]" = queue.LifoQueue()
POOL_MAX = 10


def _alive(conn: Any) -> bool:
    try:
        if psycopg is not None and isinstance(conn, psycopg.Connection):
            if conn.closed or conn.broken:
                return False
            conn.execute("SELECT 1")  # hosted servers drop idle connections
        else:
            conn.ping(reconnect=True)
        return True
    except DRIVER_ERRORS:
        return False


@contextlib.contextmanager
def connection() -> Iterator[Any]:
    try:
        conn = _pool.get_nowait()
        if not _alive(conn):
            with contextlib.suppress(Exception):
                conn.close()
            conn = _connect()
    except queue.Empty:
        conn = _connect()
    reusable = True
    try:
        yield conn
    except DRIVER_ERRORS as e:
        reusable = False
        raise DatabaseError(f"Erreur base de données : {e}") from e
    finally:
        if reusable and _pool.qsize() < POOL_MAX:
            _pool.put(conn)
        else:
            conn.close()


_init_lock = threading.Lock()
_initialized = False


def ensure_initialized() -> None:
    """Creates the schema once per process (serverless hosts may skip the startup hook)."""
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if not _initialized:
            init_db()


def query(sql: str, params: Any = None) -> List[Dict[str, Any]]:
    ensure_initialized()
    with connection() as conn, conn.cursor() as cur:
        _run(cur, sql, params)
        return list(cur.fetchall())


def query_one(sql: str, params: Any = None) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Any = None) -> int:
    ensure_initialized()
    with connection() as conn, conn.cursor() as cur:
        return _run(cur, sql, params)


class OAuthStateStore:
    """Finished OAuth sign-ins, keyed by the popup's state id. Kept in the database so that any
    server instance (several run in parallel on serverless hosts) can answer the polling."""

    def __setitem__(self, state: str, value: Dict[str, Any]) -> None:
        params = (state[:120], json.dumps(value, ensure_ascii=False))
        if is_postgres():
            execute(
                "INSERT INTO oauth_states (state, payload) VALUES (%s, %s) "
                "ON CONFLICT (state) DO UPDATE SET payload=EXCLUDED.payload, created_at=NOW()",
                params,
            )
        else:
            execute("REPLACE INTO oauth_states (state, payload) VALUES (%s, %s)", params)
        execute("DELETE FROM oauth_states WHERE created_at < NOW() - INTERVAL 15 MINUTE")

    def get(self, state: str) -> Optional[Dict[str, Any]]:
        row = query_one("SELECT payload FROM oauth_states WHERE state=%s", (state[:120],))
        return json.loads(row["payload"]) if row else None


def to_ts(value: Any) -> Optional[int]:
    """DATETIME -> unix timestamp (seconds)."""
    if isinstance(value, datetime):
        return int(value.timestamp())
    return None


def to_iso_date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return ""


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id              VARCHAR(32)  NOT NULL PRIMARY KEY,
        email           VARCHAR(255) NOT NULL UNIQUE,
        name            VARCHAR(120) NOT NULL,
        username        VARCHAR(80)  NOT NULL DEFAULT '',
        provider        VARCHAR(20)  NOT NULL DEFAULT 'email',
        provider_uid    VARCHAR(64)  NOT NULL DEFAULT '',
        avatar          MEDIUMTEXT   NULL,
        salt            VARCHAR(64)  NOT NULL DEFAULT '',
        password_hash   VARCHAR(128) NOT NULL DEFAULT '',
        created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        last_login_at   DATETIME     NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token           VARCHAR(64)  NOT NULL PRIMARY KEY,
        user_id         VARCHAR(32)  NOT NULL,
        user_agent      VARCHAR(255) NOT NULL DEFAULT '',
        ip_address      VARCHAR(64)  NOT NULL DEFAULT '',
        login_method    VARCHAR(20)  NOT NULL DEFAULT 'email',
        created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_sessions_user (user_id),
        CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id         VARCHAR(32)  NOT NULL PRIMARY KEY,
        first_name      VARCHAR(80)  NOT NULL DEFAULT '',
        last_name       VARCHAR(80)  NOT NULL DEFAULT '',
        headline        VARCHAR(140) NOT NULL DEFAULT '',
        bio             TEXT         NULL,
        phone           VARCHAR(32)  NOT NULL DEFAULT '',
        birth_date      DATE         NULL,
        gender          VARCHAR(20)  NOT NULL DEFAULT '',
        country         VARCHAR(80)  NOT NULL DEFAULT '',
        city            VARCHAR(80)  NOT NULL DEFAULT '',
        job_title       VARCHAR(120) NOT NULL DEFAULT '',
        company         VARCHAR(120) NOT NULL DEFAULT '',
        website         VARCHAR(255) NOT NULL DEFAULT '',
        github          VARCHAR(80)  NOT NULL DEFAULT '',
        linkedin        VARCHAR(255) NOT NULL DEFAULT '',
        twitter         VARCHAR(80)  NOT NULL DEFAULT '',
        interests       JSON         NULL,
        cover_theme     VARCHAR(20)  NOT NULL DEFAULT 'nebula',
        language        VARCHAR(10)  NOT NULL DEFAULT 'fr',
        timezone        VARCHAR(64)  NOT NULL DEFAULT 'Africa/Casablanca',
        pref_theme      VARCHAR(10)  NOT NULL DEFAULT 'dark',
        pref_voice      VARCHAR(10)  NOT NULL DEFAULT 'male',
        pref_auto_tts   TINYINT(1)   NOT NULL DEFAULT 0,
        pref_web_search TINYINT(1)   NOT NULL DEFAULT 1,
        system_prompt   TEXT         NULL,
        updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        CONSTRAINT fk_profiles_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS user_stats (
        user_id          VARCHAR(32) NOT NULL PRIMARY KEY,
        messages_sent    INT         NOT NULL DEFAULT 0,
        tool_calls       INT         NOT NULL DEFAULT 0,
        voice_turns      INT         NOT NULL DEFAULT 0,
        files_uploaded   INT         NOT NULL DEFAULT 0,
        images_generated INT         NOT NULL DEFAULT 0,
        last_active_at   DATETIME    NULL,
        CONSTRAINT fk_stats_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS user_daily_usage (
        user_id   VARCHAR(32) NOT NULL,
        day       DATE        NOT NULL,
        messages  INT         NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day),
        CONSTRAINT fk_usage_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_states (
        state       VARCHAR(120) NOT NULL PRIMARY KEY,
        payload     TEXT         NOT NULL,
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS user_activity (
        id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
        user_id     VARCHAR(32)  NOT NULL,
        kind        VARCHAR(30)  NOT NULL,
        label       VARCHAR(255) NOT NULL DEFAULT '',
        ip_address  VARCHAR(64)  NOT NULL DEFAULT '',
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_activity_user (user_id, created_at),
        CONSTRAINT fk_activity_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


PG_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id              VARCHAR(32)  NOT NULL PRIMARY KEY,
        email           VARCHAR(255) NOT NULL UNIQUE,
        name            VARCHAR(120) NOT NULL,
        username        VARCHAR(80)  NOT NULL DEFAULT '',
        provider        VARCHAR(20)  NOT NULL DEFAULT 'email',
        provider_uid    VARCHAR(64)  NOT NULL DEFAULT '',
        avatar          TEXT         NULL,
        salt            VARCHAR(64)  NOT NULL DEFAULT '',
        password_hash   VARCHAR(128) NOT NULL DEFAULT '',
        created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_login_at   TIMESTAMP    NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        token           VARCHAR(64)  NOT NULL PRIMARY KEY,
        user_id         VARCHAR(32)  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        user_agent      VARCHAR(255) NOT NULL DEFAULT '',
        ip_address      VARCHAR(64)  NOT NULL DEFAULT '',
        login_method    VARCHAR(20)  NOT NULL DEFAULT 'email',
        created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id)",
    """
    CREATE TABLE IF NOT EXISTS user_profiles (
        user_id         VARCHAR(32)  NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        first_name      VARCHAR(80)  NOT NULL DEFAULT '',
        last_name       VARCHAR(80)  NOT NULL DEFAULT '',
        headline        VARCHAR(140) NOT NULL DEFAULT '',
        bio             TEXT         NULL,
        phone           VARCHAR(32)  NOT NULL DEFAULT '',
        birth_date      DATE         NULL,
        gender          VARCHAR(20)  NOT NULL DEFAULT '',
        country         VARCHAR(80)  NOT NULL DEFAULT '',
        city            VARCHAR(80)  NOT NULL DEFAULT '',
        job_title       VARCHAR(120) NOT NULL DEFAULT '',
        company         VARCHAR(120) NOT NULL DEFAULT '',
        website         VARCHAR(255) NOT NULL DEFAULT '',
        github          VARCHAR(80)  NOT NULL DEFAULT '',
        linkedin        VARCHAR(255) NOT NULL DEFAULT '',
        twitter         VARCHAR(80)  NOT NULL DEFAULT '',
        interests       TEXT         NULL,
        cover_theme     VARCHAR(20)  NOT NULL DEFAULT 'nebula',
        language        VARCHAR(10)  NOT NULL DEFAULT 'fr',
        timezone        VARCHAR(64)  NOT NULL DEFAULT 'Africa/Casablanca',
        pref_theme      VARCHAR(10)  NOT NULL DEFAULT 'dark',
        pref_voice      VARCHAR(10)  NOT NULL DEFAULT 'male',
        pref_auto_tts   SMALLINT     NOT NULL DEFAULT 0,
        pref_web_search SMALLINT     NOT NULL DEFAULT 1,
        system_prompt   TEXT         NULL,
        updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_stats (
        user_id          VARCHAR(32) NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        messages_sent    INT         NOT NULL DEFAULT 0,
        tool_calls       INT         NOT NULL DEFAULT 0,
        voice_turns      INT         NOT NULL DEFAULT 0,
        files_uploaded   INT         NOT NULL DEFAULT 0,
        images_generated INT         NOT NULL DEFAULT 0,
        last_active_at   TIMESTAMP   NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_daily_usage (
        user_id   VARCHAR(32) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        day       DATE        NOT NULL,
        messages  INT         NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, day)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_states (
        state       VARCHAR(120) NOT NULL PRIMARY KEY,
        payload     TEXT         NOT NULL,
        created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_activity (
        id          BIGSERIAL    PRIMARY KEY,
        user_id     VARCHAR(32)  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind        VARCHAR(30)  NOT NULL,
        label       VARCHAR(255) NOT NULL DEFAULT '',
        ip_address  VARCHAR(64)  NOT NULL DEFAULT '',
        created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_activity_user ON user_activity (user_id, created_at)",
]


def _migrate_legacy_json() -> int:
    """Imports accounts and sessions from the old users_db.json, then renames the file so it runs once."""
    if not os.path.exists(LEGACY_JSON_DB):
        return 0
    try:
        with open(LEGACY_JSON_DB, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except Exception:
        return 0
    imported = 0
    with connection() as conn, conn.cursor() as cur:
        for u in (legacy.get("users") or {}).values():
            if not u.get("id") or not u.get("email"):
                continue
            created = datetime.fromtimestamp(int(u.get("created_at") or time.time()))
            imported += _run(
                cur,
                """INSERT IGNORE INTO users
                   (id, email, name, username, provider, provider_uid, avatar, salt, password_hash, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    u["id"], u["email"].strip().lower(), (u.get("name") or "")[:120] or u["email"].split("@")[0],
                    u.get("username", "")[:80], u.get("provider", "email"), str(u.get("provider_uid", ""))[:64],
                    u.get("avatar", ""), u.get("salt", ""), u.get("password_hash", ""), created,
                ),
            )
        for token, s in (legacy.get("sessions") or {}).items():
            _run(
                cur,
                """INSERT IGNORE INTO sessions (token, user_id, login_method, created_at)
                   SELECT %s, id, provider, %s FROM users WHERE id=%s""",
                (token[:64], datetime.fromtimestamp(int(s.get("created_at") or time.time())), s.get("user_id", "")),
            )
    os.replace(LEGACY_JSON_DB, LEGACY_JSON_DB.replace(".json", ".migrated.json"))
    return imported


def init_db() -> None:
    global _initialized
    _initialized = True  # set first: the statements below go through execute()
    try:
        _create_schema()
    except Exception:
        _initialized = False
        raise


def _create_schema() -> None:
    pg = is_postgres()
    if not pg:
        cfg = _config()
        server = _connect(with_db=False)
        try:
            with server.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{cfg['database']}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        except pymysql.MySQLError:
            pass  # hosted databases often forbid CREATE DATABASE; the database already exists there
        finally:
            server.close()
    with connection() as conn, conn.cursor() as cur:
        for stmt in PG_SCHEMA if pg else SCHEMA:
            cur.execute(stmt)
        if not pg:
            cur.execute("ALTER TABLE user_profiles ALTER cover_theme SET DEFAULT 'nebula'")
            # Generated initials avatars follow the brand colour.
            cur.execute(
                "UPDATE users SET avatar=REPLACE(avatar, 'background=2563eb', 'background=7c3aed') "
                "WHERE avatar LIKE 'https://ui-avatars.com/%background=2563eb%'"
            )
    if not pg:
        imported = _migrate_legacy_json()
        if imported:
            print(f"[Vasco] {imported} compte(s) importé(s) de users_db.json vers MySQL.")
    # Every user gets a profile + stats row.
    first_name = "SPLIT_PART(name, ' ', 1)" if pg else "SUBSTRING_INDEX(name, ' ', 1)"
    execute(
        f"""INSERT IGNORE INTO user_profiles (user_id, first_name, last_name)
           SELECT id, {first_name}, TRIM(SUBSTRING(name, CHAR_LENGTH({first_name}) + 1))
           FROM users"""
    )
    execute("INSERT IGNORE INTO user_stats (user_id) SELECT id FROM users")
