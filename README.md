# FINOVA — Goal-Based Personal Finance Planning Engine

Flask + vanilla JS web app. **Python calculates; the LLM (Gemini) parses language, reasons and explains.**

## Run locally
    pip install -r requirements.txt
    cp .env.example .env        # add GEMINI_API_KEY
    python app.py               # http://localhost:5000

## Deploy on Render
1. Push this folder to GitHub, then Render -> New -> Blueprint (reads `render.yaml`).
2. In the dashboard set `GEMINI_API_KEY` (Google AI Studio) and `ADMIN_EMAILS`. The key is read server-side only.
3. `render.yaml` uses the **free** instance type and no disk, so it costs nothing. Trade-off: the free filesystem is wiped when the
   service restarts, spins down after idle time, or is redeployed, so accounts, plans and ratings are temporary (fine for a demo/class).
   To keep data you must pay for a Render Disk (set `DATA_DIR` to its mount path) or move the database to an external service.
4. Remove `ALLOW_DEMO_RESET` and set the `SMTP_*` variables when you want real emailed reset codes.

Without `GEMINI_API_KEY` everything still works: Python computes all figures and explanations are rule-based. The UI says so.

## Photos
The 6 supplied photos are bundled in `static/img/scenes/1.jpg ... 6.jpg` (1 = the main photo of the public home page). Replace a file to change a picture. Nothing is downloaded at build time.

## Keep accounts permanently (free)
A free Render web service **loses every local file whenever it sleeps, restarts or redeploys**, so a SQLite file there makes accounts disappear.
The fix is a free external PostgreSQL database; FINOVA uses it automatically when `DATABASE_URL` is set:
1. Create a free PostgreSQL database at a provider such as Neon (neon.tech; check the provider's current free-tier terms), and copy its connection string
   (looks like `postgresql://user:password@host/dbname?sslmode=require`).
2. In Render: your service -> Environment -> add `DATABASE_URL` = that string -> save (Render redeploys).
3. Log in with an email listed in `ADMIN_EMAILS` -> Contact & Support -> Admin tab -> **Test storage**. It must say "PostgreSQL (permanent)" with all checks passed.
Do **not** use a database created from `render.yaml` (that would be a billed resource). If `DATABASE_URL` is wrong or unreachable the site keeps working on a temporary
SQLite file and shows a warning on the login page; the admin test shows the reason.
Ticket screenshots are stored in the database too. Tested only against a strict fake PostgreSQL driver, not a real server: run the admin test after you connect.

## Staying free on Render
The Blueprint contains exactly one `web` service on `plan: free`, no disk, no database, no worker. `tests/test_deploy.py` fails if anyone adds a paid
plan, `disk`, database, worker, cron or key-value store. Do not add those. Free web services sleep when idle and lose local files on restart,
so accounts, plans and ratings are temporary.

## The four LLM tasks (all validated by Python before use)
| Task | Where | Python validation |
|---|---|---|
| 1 Parse/validate inputs | Financial Information: Excel upload | rows the LLM maps from unrecognised Excel labels are accepted only after Python validation (type, range, whitelist) |
| 2 Reason + prioritise goals | Savings & Investment | LLM must restate the exact Python ranking order and mention the primary goal; figures must be grounded |
| 3 Trade-offs of what-if / stress tests | Results, Savings | figures must match engine outputs; ungrounded fields are discarded |
| 4 RAG chatbot | Contact & Support | BM25 over `data/docs.md` + FAQ, plus the signed-in user's own data only; verbatim system instructions in `ai.py` |

Errors (timeout, 429, 5xx, bad key, blocked, invalid JSON) are retried where sensible, never leak the key, and fall back to Python-generated explanations with a visible notice.

## Tests
    python -m unittest discover -s tests -p "test_*.py"        # tests: engine, exact-arithmetic verification, API, security, mock-Gemini
    pip install playwright && playwright install chromium
    python tests/e2e_server.py &  python tests/e2e_browser.py   # full browser walk-through against a mock Gemini

`tests/mock_gemini.py` is a local fake of the Gemini endpoint with failure modes. **No test uses a real Gemini key**; test with your real key after deploying.

## Structure
`engine.py` calculations/validation/Excel · `verify.py` independent checks · `narrative.py` rule-based text · `ai.py` Gemini + RAG · `app.py` API/security · `static/` front end · `data/` FAQ + docs
