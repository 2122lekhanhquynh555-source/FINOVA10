"""Storage layer.

* Default: a SQLite file (fine on your own computer or on a host with a persistent disk).
* Permanent storage on free hosting: set DATABASE_URL to a PostgreSQL connection string (for example a free Neon database).
  Free web services lose their local files whenever they sleep or restart, so data kept only in a local file disappears.

All application SQL is written once with '?' placeholders; this module adapts it for PostgreSQL.
If DATABASE_URL is set but cannot be used, the app keeps running on SQLite and reports the reason (see Store.startup_error).
"""
import logging
import os
import re
import sqlite3
import time
from urllib.parse import urlparse

log = logging.getLogger("finova.store")
IntegrityError = sqlite3.IntegrityError          # PostgreSQL integrity errors are re-raised as this class

TABLES = """
CREATE TABLE IF NOT EXISTS users(id {PK}, name TEXT, email TEXT UNIQUE, pw TEXT, age INTEGER, gender TEXT, country TEXT, province TEXT, phone TEXT, occupation TEXT, created {REAL});
CREATE TABLE IF NOT EXISTS finance(user_id {FK} PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, data TEXT, updated {REAL});
CREATE TABLE IF NOT EXISTS plans(id {PK}, user_id {FK} REFERENCES users(id) ON DELETE CASCADE, name TEXT, data TEXT, created {REAL}, updated {REAL});
CREATE TABLE IF NOT EXISTS ratings(user_id {FK} PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, accuracy INTEGER, groundedness INTEGER, usability INTEGER, processing INTEGER, avg {REAL}, comment TEXT, flagged INTEGER, updated {REAL});
CREATE TABLE IF NOT EXISTS metrics(id {PK}, name TEXT, ms {REAL}, created {REAL});
CREATE TABLE IF NOT EXISTS llm_log(id {PK}, task TEXT, source TEXT, error TEXT, ms {REAL}, g_ok INTEGER, g_checked INTEGER, rejected INTEGER, created {REAL});
CREATE TABLE IF NOT EXISTS events(id {PK}, user_id {FK}, name TEXT, created {REAL});
CREATE TABLE IF NOT EXISTS attempts(id {PK}, k TEXT, created {REAL});
CREATE TABLE IF NOT EXISTS resets(user_id {FK} PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, code TEXT, expires {REAL}, tries INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS drafts(user_id {FK} REFERENCES users(id) ON DELETE CASCADE, kind TEXT, data TEXT, updated {REAL}, PRIMARY KEY(user_id, kind));
CREATE TABLE IF NOT EXISTS tickets(id {PK}, user_id {FK} REFERENCES users(id) ON DELETE CASCADE, type TEXT, subject TEXT, description TEXT, email TEXT, attachment TEXT, status TEXT DEFAULT 'open', note TEXT, created {REAL}, updated {REAL});
CREATE TABLE IF NOT EXISTS attachments(ticket_id {FK} PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE, mime TEXT, data {BLOB});
CREATE INDEX IF NOT EXISTS i_plans ON plans(user_id);
CREATE INDEX IF NOT EXISTS i_att ON attempts(k, created);
CREATE INDEX IF NOT EXISTS i_ev ON events(user_id, name);
CREATE INDEX IF NOT EXISTS i_tk ON tickets(user_id)
"""
TYPES = {"sqlite": {"PK": "INTEGER PRIMARY KEY", "FK": "INTEGER", "REAL": "REAL", "BLOB": "BLOB"},
         "postgres": {"PK": "BIGSERIAL PRIMARY KEY", "FK": "BIGINT", "REAL": "DOUBLE PRECISION", "BLOB": "BYTEA"}}


def schema_statements(kind):
    t = TYPES[kind]
    return [s.strip().format(**t) for s in TABLES.split(";") if s.strip()]


def mask(text, url=""):
    """Remove the database password / URL from any message before it is logged or shown."""
    text = str(text)
    if url:
        text = text.replace(url, "<DATABASE_URL>")
        pw = urlparse(url).password
        if pw:
            text = text.replace(pw, "***")
    return re.sub(r"(postgres(?:ql)?://)[^@\s]+@", r"\1***@", text)[:300]


class Cur:
    def __init__(self, cur, kind):
        self._c, self.kind = cur, kind

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c.fetchall())

    @property
    def rowcount(self):
        return self._c.rowcount


class Conn:
    """A connection with one tiny API for both databases: execute(sql, params), insert(sql, params), commit(), close()."""

    def __init__(self, raw, kind, mod=None, url=""):
        self.raw, self.kind, self.mod, self.url = raw, kind, mod, url

    def execute(self, sql, params=()):
        if self.kind == "sqlite":
            return Cur(self.raw.execute(sql, tuple(params)), "sqlite")
        cur = self.raw.cursor()
        try:
            cur.execute(sql.replace("%", "%%").replace("?", "%s"), tuple(params))
        except Exception as e:
            try:
                self.raw.rollback()                       # PostgreSQL refuses further statements after an error until rollback
            except Exception:
                pass
            if isinstance(e, self.mod.IntegrityError):
                raise IntegrityError(mask(e, self.url)) from None
            raise
        return Cur(cur, "postgres")

    def insert(self, sql, params=()):
        """INSERT a row into a table with an `id` column and return the new id."""
        if self.kind == "sqlite":
            return self.raw.execute(sql, tuple(params)).lastrowid
        return self.execute(sql + " RETURNING id", params).fetchone()["id"]

    def blob(self, data):
        return self.mod.Binary(data) if self.kind == "postgres" else bytes(data)

    def commit(self):
        self.raw.commit()

    def close(self):
        try:
            if self.kind == "postgres":
                self.raw.rollback()
        except Exception:
            pass
        self.raw.close()


class Store:
    def __init__(self, data_dir):
        self.sqlite_path = os.getenv("DATABASE_PATH") or str(data_dir / "finova.db")
        self.url = os.getenv("DATABASE_URL", "").strip()
        self.backend, self.startup_error, self._pg = "sqlite", None, None
        if self.url:
            try:
                import psycopg2
                import psycopg2.extras
                self._pg = psycopg2
                self.backend = "postgres"
                self._init_schema()
                log.warning("Storage: PostgreSQL (permanent).")
            except Exception as e:
                self.startup_error = f"{type(e).__name__}: {mask(e, self.url)}"
                self.backend, self._pg = "sqlite", None
                log.error("DATABASE_URL could not be used, falling back to SQLite (temporary on free hosting): %s", self.startup_error)
        if self.backend == "sqlite":
            self._init_schema()
            if not self.url:
                log.warning("Storage: SQLite file %s (temporary on free hosting).", self.sqlite_path)

    # ------------------------------------------------------------------
    def connect(self):
        if self.backend == "sqlite":
            raw = sqlite3.connect(self.sqlite_path, timeout=15)
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA foreign_keys=ON")
            return Conn(raw, "sqlite")
        import psycopg2.extras
        raw = self._pg.connect(self.url, connect_timeout=15, cursor_factory=psycopg2.extras.RealDictCursor)
        return Conn(raw, "postgres", self._pg, self.url)

    def _init_schema(self):
        if self.backend == "sqlite":
            raw = sqlite3.connect(self.sqlite_path, timeout=30)
            raw.execute("PRAGMA journal_mode=WAL")
            raw.close()
        c = self.connect()
        try:
            for stmt in schema_statements(self.backend):
                c.execute(stmt)
            c.commit()
        finally:
            c.close()

    @property
    def persistent(self):
        return self.backend == "postgres"

    @property
    def ephemeral_warning(self):
        """True when data lives in a local file on a host that wipes files (Render)."""
        return (not self.persistent) and bool(os.getenv("RENDER") or os.getenv("FORCE_EPHEMERAL_WARNING")) and not os.getenv("HIDE_STORAGE_WARNING")

    def status(self):
        return {"backend": self.backend, "persistent": self.persistent, "database_url_set": bool(self.url), "startup_error": self.startup_error}

    def check(self):
        """Live read/write test (used by the admin 'Test storage' button)."""
        steps = []

        def step(name, fn):
            try:
                out = fn()
                steps.append({"step": name, "ok": True, "detail": "" if out is None else str(out)[:120]})
                return True, out
            except Exception as e:
                steps.append({"step": name, "ok": False, "detail": f"{type(e).__name__}: {mask(e, self.url)}"})
                return False, None

        t0 = time.perf_counter()
        ok, c = step("connect", lambda: self.connect())
        if not ok:
            return {**self.status(), "ok": False, "steps": steps}
        try:
            ddl = "CREATE TABLE IF NOT EXISTS _finova_check(id {PK}, v TEXT)".format(**TYPES[self.backend])
            ok, _ = step("create table", lambda: (c.execute(ddl), c.commit()) and "ok")
            if ok:
                ok, _ = step("insert and read back", lambda: (c.insert("INSERT INTO _finova_check(v) VALUES(?)", ("hello",)), c.commit(),
                                                              c.execute("SELECT v FROM _finova_check").fetchall()[-1]["v"])[-1])
            if ok:
                ok, _ = step("duplicate key is rejected", lambda: self._dup_test(c))
            if ok:
                step("users stored", lambda: c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])
        finally:
            step("clean up", lambda: (c.execute("DROP TABLE IF EXISTS _finova_check"), c.commit()) and "ok")
            c.close()
        return {**self.status(), "ok": all(x["ok"] for x in steps), "steps": steps, "ms": round((time.perf_counter() - t0) * 1000)}

    @staticmethod
    def _dup_test(c):
        c.execute("DELETE FROM users WHERE email=?", ("__finova_check__",))
        c.insert("INSERT INTO users(email,name) VALUES(?,?)", ("__finova_check__", "check"))
        try:
            c.insert("INSERT INTO users(email,name) VALUES(?,?)", ("__finova_check__", "check"))
            raise RuntimeError("duplicate email was accepted")
        except IntegrityError:
            pass
        finally:
            c.execute("DELETE FROM users WHERE email=?", ("__finova_check__",))
            c.commit()
        return "ok"
