"""A stand-in for psycopg2 used in tests (no PostgreSQL server is available offline).

It stores data in a SQLite file but is deliberately STRICT like PostgreSQL where the app could go wrong:
  * only %s placeholders are accepted (a leftover '?' is an error); SQLite-only syntax (PRAGMA, AUTOINCREMENT, INSERT OR, lastrowid) is rejected
  * a subquery in FROM must have an alias
  * after any error the transaction is 'aborted': every statement fails until rollback() is called
  * rows are dicts (like RealDictCursor) and BYTEA values come back as memoryview
  * DDL uses the PostgreSQL type names (BIGSERIAL, DOUBLE PRECISION, BIGINT, BYTEA), mapped to SQLite equivalents
It cannot prove the SQL is valid on a real PostgreSQL server."""
import os
import re
import sqlite3
import types


class Error(Exception):
    pass


class OperationalError(Error):
    pass


class ProgrammingError(Error):
    pass


class IntegrityError(Error):
    pass


class InFailedSqlTransaction(ProgrammingError):
    pass


class Binary:
    def __init__(self, b):
        self.b = bytes(b)


sqlite3.register_adapter(Binary, lambda x: x.b)


class RealDictCursor:
    pass


extras = types.ModuleType("psycopg2.extras")
extras.RealDictCursor = RealDictCursor
_TYPES = [(r"\bBIGSERIAL PRIMARY KEY\b", "INTEGER PRIMARY KEY"), (r"\bDOUBLE PRECISION\b", "REAL"), (r"\bBIGINT\b", "INTEGER"), (r"\bBYTEA\b", "BLOB")]
_BANNED = [r"\bPRAGMA\b", r"\bAUTOINCREMENT\b", r"\bINSERT\s+OR\b", r"\blastrowid\b", r"\bREPLACE\s+INTO\b"]
stats = {"statements": 0}


class _Conn:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.failed = False

    def cursor(self, cursor_factory=None):
        return _Cur(self)

    def commit(self):
        if self.failed:
            self.db.rollback()
            self.failed = False
        else:
            self.db.commit()

    def rollback(self):
        self.db.rollback()
        self.failed = False

    def close(self):
        self.db.close()


class _Cur:
    def __init__(self, conn):
        self.conn, self._rows, self.rowcount, self._cols = conn, [], -1, []

    def execute(self, sql, params=()):
        c = self.conn
        if c.failed:
            raise InFailedSqlTransaction("current transaction is aborted, commands ignored until end of transaction block")
        if "?" in sql:
            raise ProgrammingError("a '?' placeholder reached PostgreSQL; only %s is valid")
        for pat in _BANNED:
            if re.search(pat, sql, re.I):
                raise ProgrammingError(f"SQLite-only syntax is not valid on PostgreSQL: {pat}")
        if re.search(r"FROM\s*\(", sql, re.I) and not re.search(r"\)\s+(AS\s+)?\w+\s*$", sql.strip(), re.I):
            raise ProgrammingError("subquery in FROM must have an alias")
        q = sql
        if re.match(r"\s*CREATE", q, re.I):
            for pat, rep in _TYPES:
                q = re.sub(pat, rep, q)
        q = q.replace("%s", "?").replace("%%", "%")
        stats["statements"] += 1
        try:
            cur = c.db.execute(q, tuple(params))
        except sqlite3.IntegrityError as e:
            c.failed = True
            raise IntegrityError(str(e))
        except sqlite3.Error as e:
            c.failed = True
            raise ProgrammingError(str(e))
        self.rowcount = cur.rowcount
        self._cols = [d[0] for d in (cur.description or [])]
        self._rows = cur.fetchall() if cur.description else []

    def _row(self, r):
        return {k: (memoryview(v) if isinstance(v, bytes) else v) for k, v in zip(self._cols, r)}

    def fetchone(self):
        return self._row(self._rows.pop(0)) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return [self._row(r) for r in rows]


def connect(dsn, connect_timeout=None, cursor_factory=None):
    if os.environ.get("FAKE_PG_FAIL"):
        raise OperationalError(f'could not connect to server using "{dsn}": connection refused')
    if not re.match(r"postgres(ql)?://", dsn):
        raise OperationalError("invalid connection string")
    return _Conn(os.environ["FAKE_PG_FILE"])
