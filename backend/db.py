"""MySQL storage for Vasco: users, sessions, profiles, preferences, usage stats and activity."""
import contextlib
import json
import os
import queue
import time
import urllib.parse
from datetime import date, datetime
from typing import Any, Dict, Iterator, List, Optional

import pymysql
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
LEGACY_JSON_DB = os.path.join(os.path.dirname(__file__), "users_db.json")
load_dotenv(ENV_PATH)


class DatabaseError(RuntimeError):
    pass


def _config() -> Dict[str, Any]:
    # Hosted MySQL services (Railway, Aiven…) give a single URL: mysql://user:password@host:port/database
    url = (os.getenv("MYSQL_URL") or "").strip()
    if url:
        u = urllib.parse.urlparse(url)
        return {
            "host": u.hostname or "127.0.0.1",
            "port": u.port or 3306,
            "user": urllib.parse.unquote(u.username or "root"),
            "password": urllib.parse.unquote(u.password or ""),
            "database": (u.path or "/").lstrip("/") or os.getenv("MYSQL_DATABASE", "Vasco").strip(),
        }
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1").strip(),
        "port": int(os.getenv("MYSQL_PORT", "3306").strip() or 3306),
        "user": os.getenv("MYSQL_USER", "root").strip(),
        "password": os.getenv("MYSQL_PASSWORD", "1234"),
        "database": os.getenv("MYSQL_DATABASE", "Vasco").strip(),
    }


def _connect(with_db: bool = True) -> pymysql.connections.Connection:
    cfg = _config()
    kwargs = dict(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=True,
        connect_timeout=5,
    )
    if with_db:
        kwargs["database"] = cfg["database"]
    try:
        return pymysql.connect(**kwargs)
    except pymysql.MySQLError as e:
        raise DatabaseError(
            f"Connexion MySQL impossible ({cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['database']}) : {e}"
        ) from e


# Opening a MySQL connection costs ~300 ms on Windows: reuse them.
_pool: "queue.LifoQueue[pymysql.connections.Connection]" = queue.LifoQueue()
POOL_MAX = 10


@contextlib.contextmanager
def connection() -> Iterator[pymysql.connections.Connection]:
    try:
        conn = _pool.get_nowait()
        try:
            conn.ping(reconnect=True)
        except pymysql.MySQLError:
            conn = _connect()
    except queue.Empty:
        conn = _connect()
    reusable = True
    try:
        yield conn
    except pymysql.MySQLError as e:
        reusable = False
        raise DatabaseError(f"Erreur MySQL : {e}") from e
    finally:
        if reusable and _pool.qsize() < POOL_MAX:
            _pool.put(conn)
        else:
            conn.close()


def query(sql: str, params: Any = None) -> List[Dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def query_one(sql: str, params: Any = None) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Any = None) -> int:
    with connection() as conn, conn.cursor() as cur:
        return cur.execute(sql, params)


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
            imported += cur.execute(
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
            cur.execute(
                """INSERT IGNORE INTO sessions (token, user_id, login_method, created_at)
                   SELECT %s, id, provider, %s FROM users WHERE id=%s""",
                (token[:64], datetime.fromtimestamp(int(s.get("created_at") or time.time())), s.get("user_id", "")),
            )
    os.replace(LEGACY_JSON_DB, LEGACY_JSON_DB.replace(".json", ".migrated.json"))
    return imported


def init_db() -> None:
    cfg = _config()
    server = _connect(with_db=False)
    try:
        with server.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{cfg['database']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        server.close()
    with connection() as conn, conn.cursor() as cur:
        for stmt in SCHEMA:
            cur.execute(stmt)
        cur.execute("ALTER TABLE user_profiles ALTER cover_theme SET DEFAULT 'nebula'")
        # Generated initials avatars follow the brand colour.
        cur.execute(
            "UPDATE users SET avatar=REPLACE(avatar, 'background=2563eb', 'background=7c3aed') "
            "WHERE avatar LIKE 'https://ui-avatars.com/%background=2563eb%'"
        )
    imported = _migrate_legacy_json()
    if imported:
        print(f"[Vasco] {imported} compte(s) importé(s) de users_db.json vers MySQL.")
    # Every user gets a profile + stats row.
    execute(
        """INSERT IGNORE INTO user_profiles (user_id, first_name, last_name)
           SELECT id, SUBSTRING_INDEX(name, ' ', 1),
                  TRIM(SUBSTRING(name, CHAR_LENGTH(SUBSTRING_INDEX(name, ' ', 1)) + 1))
           FROM users"""
    )
    execute("INSERT IGNORE INTO user_stats (user_id) SELECT id FROM users")
