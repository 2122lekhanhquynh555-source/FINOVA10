"""Simulates a free-hosting restart: the local disk is wiped between phase 1 and phase 2; only the external database survives.
usage: persist_scenario.py <register|login> <data_dir> <pg|sqlite>   (prints one JSON line)"""
import io, json, os, sys
phase, data_dir, mode = sys.argv[1:4]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(DATA_DIR=data_dir, REGISTER_LIMIT="1000")
os.environ.pop("GEMINI_API_KEY", None)
if mode == "pg":
    os.environ["DATABASE_URL"] = "postgresql://u:pw@fake-host/db?sslmode=require"
    from tests import fake_psycopg2 as fp
    sys.modules["psycopg2"], sys.modules["psycopg2.extras"] = fp, fp.extras
import app as A
c = A.app.test_client(); tok = c.get("/api/me").get_json()["csrf"]
H = lambda: {"X-CSRF-Token": tok}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01\x02\x03" * 40
FIN = {"income": 25e6, "expenses": 12e6, "investments": 50e6, "assets": 120e6, "debt": 80e6, "debt_payment": 3e6, "years": 10, "liquidity": "medium", "risk": "medium", "debt_rate": 8}
out = {"backend": A.STORE.backend}
if phase == "register":
    r = c.post("/api/register", json={"name": "Nga", "email": "nga@x.com", "password": "abc12345", "confirm": "abc12345"}, headers=H()); out["register"] = r.status_code; tok = r.get_json()["csrf"]
    out["finance"] = c.put("/api/finance", json=FIN, headers=H()).status_code
    out["plan"] = c.post("/api/plans", json={"name": "Home", "goals": [{"name": "Home", "target": 5e8, "current": 0, "years": 8}]}, headers=H()).status_code
    out["draft"] = c.put("/api/goals/draft", json={"goals": [{"name": "Saved goal", "target": 3e8, "years": 6}]}, headers=H()).status_code
    out["rating"] = c.post("/api/ratings", json={"accuracy": 5, "groundedness": 4, "usability": 5, "processing": 4}, headers=H()).status_code
    out["ticket"] = c.post("/api/support", data={"type": "Technical Issues", "subject": "Hello", "description": "A screenshot attached here.", "email": "nga@x.com", "attachment": (io.BytesIO(PNG), "s.png")}, headers=H(), content_type="multipart/form-data").status_code
else:
    r = c.post("/api/login", json={"email": "nga@x.com", "password": "abc12345"}, headers=H()); out["login"] = r.status_code
    if r.status_code == 200:
        tok = r.get_json()["csrf"]
        out["income"] = c.get("/api/finance").get_json()["finance"]["income"]
        out["plans"] = [p["name"] for p in c.get("/api/plans").get_json()["plans"]]
        out["draft"] = c.get("/api/goals/draft").get_json()["goals"][0]["name"]
        out["rated_again"] = c.post("/api/ratings", json={"accuracy": 1, "groundedness": 1, "usability": 1, "processing": 1}, headers=H()).status_code
        t = c.get("/api/support").get_json()["tickets"][0]; a = c.get(f"/api/support/{t['id']}/attachment")
        out["attachment"] = [a.status_code, a.data == PNG, a.mimetype]
print(json.dumps(out))
