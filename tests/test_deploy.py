"""Deployment safety: the Blueprint must only create FREE resources, and the photo downloader must never break a build."""
import re, unittest
from pathlib import Path

import yaml
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


class BlueprintIsFree(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "render.yaml").read_text()
        self.cfg = yaml.safe_load(self.text)

    def test_only_one_free_web_service(self):
        self.assertEqual(set(self.cfg) - {"services"}, set(), "no databases / keyvalue / envVarGroups / previews / paid top-level resources")
        svcs = self.cfg["services"]
        self.assertEqual(len(svcs), 1)
        s = svcs[0]
        self.assertEqual(s["type"], "web"); self.assertEqual(s["plan"], "free")
        for forbidden in ("disk", "disks", "numInstances", "scaling", "autoscaling", "maxShutdownDelaySeconds"):
            self.assertNotIn(forbidden, s, forbidden)

    def test_no_paid_keywords_anywhere(self):
        code = "\n".join(re.sub(r"\s#.*$", "", l) for l in self.text.splitlines() if not l.strip().startswith("#"))
        for word in ("starter", "standard", "pro", "plus", "pserv", "worker", "cron", "redis", "keyvalue", "postgres", "databases", "disk:"):
            self.assertIsNone(re.search(rf"\b{re.escape(word)}\b", code), word)

    def test_start_and_build_commands(self):
        s = self.cfg["services"][0]
        self.assertIn("gunicorn app:app", s["startCommand"]); self.assertIn("$PORT", s["startCommand"])
        self.assertEqual(s["buildCommand"].strip(), "pip install -r requirements.txt")                           # nothing else runs at build time
        keys = {e["key"]: e for e in s["envVars"]}
        self.assertTrue(keys["SECRET_KEY"].get("generateValue")); self.assertTrue(keys["GEMINI_API_KEY"].get("sync") is False)
        self.assertNotIn("value", keys["GEMINI_API_KEY"])                                                       # no secret committed
        self.assertIs(keys["DATABASE_URL"].get("sync"), False); self.assertNotIn("value", keys["DATABASE_URL"])      # the database URL (with its password) is never committed

    def test_requirements_complete(self):
        req = (ROOT / "requirements.txt").read_text().lower()
        for pkg in ("flask", "gunicorn", "openpyxl", "requests", "psycopg2"):
            self.assertIn(pkg, req)


class PhotosShipped(unittest.TestCase):
    def test_exactly_the_six_supplied_photos_are_bundled(self):
        d = ROOT / "static" / "img" / "scenes"
        self.assertEqual(sorted(p.name for p in d.iterdir()), [f"{i}.jpg" for i in range(1, 7)])          # no leftovers of the old pictures
        for f in d.glob("*.jpg"):
            im = Image.open(f); self.assertEqual(im.format, "JPEG"); self.assertGreaterEqual(im.size[0], 1200); self.assertGreater(im.size[0], im.size[1])
            self.assertLess(f.stat().st_size, 400_000)

    def test_site_uses_only_these_photos(self):
        js = "\n".join(p.read_text() for p in (ROOT / "static" / "js").glob("*.js")) + (ROOT / "static" / "index.html").read_text()
        for stale in ("wikimedia", "data:image/svg", "halong", "7.jpg", "8.jpg", "tools/fetch_photos"):
            self.assertNotIn(stale, js.lower() if stale != "data:image/svg" else js, stale)
        self.assertEqual(re.findall(r"/static/img/(?!scenes/|logo\.png)[^\s'\"`)]*", js), [])             # nothing but the 6 photos and the logo
        self.assertFalse((ROOT / "tools").exists())


class RemovedFeatures(unittest.TestCase):
    def test_discover_vietnam_and_fill_the_form_are_gone(self):
        blob = "\n".join(p.read_text() for p in list((ROOT / "static").rglob("*.js")) + list((ROOT / "static").rglob("*.css")) + [ROOT / "static" / "index.html", ROOT / "app.py", ROOT / "ai.py"])
        for gone in ("Discover Vietnam", "gallery", "Fill the form", "aiParse", "aiSave", "Describe your finances", "/api/ai/parse", "parse_text", "rule_parse"):
            self.assertNotIn(gone, blob, gone)
        self.assertNotIn("Fill the form", (ROOT / "data" / "faq.json").read_text() + (ROOT / "data" / "docs.md").read_text())


if __name__ == "__main__":
    unittest.main()
