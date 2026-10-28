"""Permanent-storage tests. A strict fake driver stands in for PostgreSQL (no server is available offline), so these tests prove the
app's code paths, placeholder translation, error handling and restart behaviour - not that the SQL is valid on a real PostgreSQL server."""
import json, os, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import store
from tests import fake_psycopg2 as fp


def run(args, env=None):
    return subprocess.run([sys.executable] + args, cwd=ROOT, env={**os.environ, **(env or {})}, capture_output=True, text=True, timeout=300)


class WholeAppOnPostgresPath(unittest.TestCase):
    def test_full_api_suite_passes_on_the_postgres_code_path(self):
        r = run(["-W", "ignore", "-m", "unittest", "tests.test_api"], {"FINOVA_TEST_BACKEND": "pg"})
        self.assertEqual(r.returncode, 0, r.stderr[-1500:]); self.assertIn("OK", r.stderr)


class SurvivesRestart(unittest.TestCase):
    """The reported bug: after the free server slept, accounts were gone."""

    def phase(self, phase, data_dir, mode, fake_file):
        r = run(["tests/persist_scenario.py", phase, data_dir, mode], {"FAKE_PG_FILE": fake_file})
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_with_database_url_everything_survives_a_wiped_disk(self):
        tmp = tempfile.mkdtemp(); fake = os.path.join(tmp, "external_db_survives.db")
        d1, d2 = os.path.join(tmp, "disk1"), os.path.join(tmp, "disk2")
        a = self.phase("register", d1, "pg", fake)
        self.assertEqual(a["backend"], "postgres"); self.assertEqual([a[k] for k in ("register", "finance", "plan", "draft", "rating", "ticket")], [200, 200, 200, 200, 200, 200])
        shutil.rmtree(d1)                                                         # Render wipes the local disk when the service sleeps
        b = self.phase("login", d2, "pg", fake)                                   # brand-new empty disk, same external database
        self.assertEqual(b["login"], 200); self.assertEqual(b["income"], 25e6); self.assertEqual(b["plans"], ["Home"]); self.assertEqual(b["draft"], "Saved goal")
        self.assertEqual(b["rated_again"], 409); self.assertEqual(b["attachment"], [200, True, "image/png"])

    def test_without_it_the_account_is_lost_like_before(self):
        tmp = tempfile.mkdtemp(); d1, d2 = os.path.join(tmp, "disk1"), os.path.join(tmp, "disk2")
        self.phase("register", d1, "sqlite", ""); shutil.rmtree(d1)
        self.assertEqual(self.phase("login", d2, "sqlite", "")["login"], 401)     # documents why DATABASE_URL is needed on a free host


class Fallback(unittest.TestCase):
    def tearDown(self):
        for k in ("DATABASE_URL", "FAKE_PG_FAIL", "RENDER", "FORCE_EPHEMERAL_WARNING", "HIDE_STORAGE_WARNING"):
            os.environ.pop(k, None)
        sys.modules.pop("psycopg2", None); sys.modules.pop("psycopg2.extras", None)

    def test_driver_missing_falls_back_to_sqlite_and_hides_the_password(self):
        os.environ["DATABASE_URL"] = "postgresql://u:TopSecret@host/db"; sys.modules["psycopg2"] = None      # import fails
        s = store.Store(Path(tempfile.mkdtemp()))
        self.assertEqual(s.backend, "sqlite"); self.assertFalse(s.persistent); self.assertTrue(s.startup_error); self.assertNotIn("TopSecret", s.startup_error)

    def test_unreachable_database_falls_back_and_masks_secrets(self):
        os.environ.update(DATABASE_URL="postgresql://u:TopSecret@host/db", FAKE_PG_FILE=tempfile.mktemp(), FAKE_PG_FAIL="1")
        sys.modules["psycopg2"], sys.modules["psycopg2.extras"] = fp, fp.extras
        s = store.Store(Path(tempfile.mkdtemp()))
        self.assertEqual(s.backend, "sqlite"); self.assertIn("OperationalError", s.startup_error); self.assertNotIn("TopSecret", s.startup_error); self.assertNotIn("postgresql://", s.startup_error)
        c = s.check(); self.assertTrue(c["ok"]); self.assertEqual(c["backend"], "sqlite"); self.assertEqual(c["startup_error"], s.startup_error)        # site still works

    def test_warning_only_when_data_is_really_temporary(self):
        d = Path(tempfile.mkdtemp())
        self.assertFalse(store.Store(d).ephemeral_warning)                                   # local computer
        os.environ["RENDER"] = "true"; self.assertTrue(store.Store(d).ephemeral_warning)     # free host without DATABASE_URL
        os.environ["HIDE_STORAGE_WARNING"] = "1"; self.assertFalse(store.Store(d).ephemeral_warning)
        del os.environ["HIDE_STORAGE_WARNING"]
        os.environ.update(DATABASE_URL="postgresql://u:p@h/d", FAKE_PG_FILE=tempfile.mktemp()); sys.modules["psycopg2"], sys.modules["psycopg2.extras"] = fp, fp.extras
        s = store.Store(d); self.assertTrue(s.persistent); self.assertFalse(s.ephemeral_warning)


class Adapter(unittest.TestCase):
    def setUp(self):
        os.environ.update(DATABASE_URL="postgresql://u:pw@h/d", FAKE_PG_FILE=tempfile.mktemp()); sys.modules["psycopg2"], sys.modules["psycopg2.extras"] = fp, fp.extras
        self.s = store.Store(Path(tempfile.mkdtemp()))

    def tearDown(self):
        for k in ("DATABASE_URL", "FAKE_PG_FILE"): os.environ.pop(k, None)
        sys.modules.pop("psycopg2", None); sys.modules.pop("psycopg2.extras", None)

    def test_schema_uses_postgres_types_only(self):
        pg = "\n".join(store.schema_statements("postgres")); lite = "\n".join(store.schema_statements("sqlite"))
        for good in ("BIGSERIAL PRIMARY KEY", "DOUBLE PRECISION", "BYTEA", "BIGINT"): self.assertIn(good, pg)
        for bad in ("INTEGER PRIMARY KEY", " REAL", "BLOB", "AUTOINCREMENT"): self.assertNotIn(bad, pg)
        for bad in ("BIGSERIAL", "DOUBLE PRECISION", "BYTEA"): self.assertNotIn(bad, lite)
        self.assertEqual(len(store.schema_statements("postgres")), 16)

    def test_insert_returns_ids_and_integrity_errors_are_uniform(self):
        c = self.s.connect()
        a = c.insert("INSERT INTO users(name,email) VALUES(?,?)", ("A", "a@x.com")); b = c.insert("INSERT INTO users(name,email) VALUES(?,?)", ("B", "b@x.com")); c.commit()
        self.assertEqual((a, b), (1, 2))
        with self.assertRaises(store.IntegrityError): c.insert("INSERT INTO users(name,email) VALUES(?,?)", ("A2", "a@x.com"))
        self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"], 2)       # the wrapper rolled back, so the connection is usable again
        c.close()

    def test_percent_signs_and_none_are_handled(self):
        c = self.s.connect(); c.insert("INSERT INTO users(name,email,age) VALUES(?,?,?)", ("100% sure", "p@x.com", None)); c.commit()
        r = c.execute("SELECT name, age FROM users WHERE email=?", ("p@x.com",)).fetchone()
        self.assertEqual((r["name"], r["age"]), ("100% sure", None)); c.close()

    def test_blobs_roundtrip_as_bytes(self):
        c = self.s.connect(); uid = c.insert("INSERT INTO users(name,email) VALUES(?,?)", ("A", "a@x.com"))
        tid = c.insert("INSERT INTO tickets(user_id,type,subject,description,email,status,created,updated) VALUES(?,?,?,?,?,'open',?,?)", (uid, "t", "s", "d", "a@x.com", 1.5, 1.5))
        c.execute("INSERT INTO attachments(ticket_id,mime,data) VALUES(?,?,?)", (tid, "image/png", c.blob(b"\x89PNG\x00\xff"))); c.commit()
        row = c.execute("SELECT mime, data FROM attachments WHERE ticket_id=?", (tid,)).fetchone()
        self.assertIsInstance(row["data"], memoryview); self.assertEqual(bytes(row["data"]), b"\x89PNG\x00\xff")       # BYTEA comes back as memoryview: app code must convert
        c.close()

    def test_fake_driver_really_is_strict(self):
        raw = fp.connect("postgresql://x", cursor_factory=fp.extras.RealDictCursor); cur = raw.cursor()
        for bad in ("SELECT ?", "PRAGMA foreign_keys=ON", "INSERT OR IGNORE INTO t VALUES(1)", "SELECT COUNT(*) c FROM (SELECT 1)"):
            with self.assertRaises(fp.ProgrammingError, msg=bad): cur.execute(bad)
        cur.execute("CREATE TABLE z(id BIGSERIAL PRIMARY KEY, v TEXT UNIQUE)"); cur.execute("INSERT INTO z(v) VALUES(%s)", ("a",))
        with self.assertRaises(fp.IntegrityError): cur.execute("INSERT INTO z(v) VALUES(%s)", ("a",))
        with self.assertRaises(fp.InFailedSqlTransaction): cur.execute("SELECT 1")             # aborted transaction
        raw.rollback(); cur.execute("SELECT 1 AS one"); self.assertEqual(cur.fetchone(), {"one": 1})


class NoSqliteLeftovers(unittest.TestCase):
    def test_app_code_is_database_neutral(self):
        for name in ("app.py", "ai.py", "narrative.py"):
            src = (ROOT / name).read_text()
            for bad in ("sqlite3", ".lastrowid", "PRAGMA", "INSERT OR", "AUTOINCREMENT", "DB_PATH", "send_from_directory(DATA_DIR"):
                self.assertNotIn(bad, src.replace("from flask import", ""), f"{name}: {bad}")

    def test_every_insert_that_needs_an_id_uses_insert(self):
        src = (ROOT / "app.py").read_text()
        self.assertEqual(len(re.findall(r'db\(\)\.insert\("INSERT INTO (users|plans|tickets)', src)), 3)


if __name__ == "__main__":
    unittest.main()
