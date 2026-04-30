# Deploying to Vercel

This Flask app runs on Vercel's Python serverless runtime. The same dashboard, Sheet sync, Shopify orders, and CSV export — no code or UI changes — just a few platform adjustments because Vercel functions are stateless and short-lived.

## Architecture summary

| Concern | Local dev | Vercel |
| --- | --- | --- |
| Web server | `flask run` / gunicorn | `@vercel/python` serverless function (`api/index.py`) |
| Database | local Postgres via `DB_*` env vars | hosted Postgres via `DATABASE_URL` |
| Google OAuth | `credentials.json` + `token.json` files | `GOOGLE_CREDENTIALS_JSON` + `GOOGLE_TOKEN_JSON` env vars |
| Inventory sync | direct call to `tools.sync_inventory_from_sheet.sync()` | same — no subprocess, no venv |
| Shopify creds | per-store env vars | same per-store env vars |

## 1. Provision a hosted Postgres

Pick one — they all give you a `postgres://...` connection URL:

- **Vercel Postgres** — Vercel dashboard → Storage → Create Postgres
- **Neon** — neon.tech (free tier, branching)
- **Supabase** — supabase.com
- **Railway** — railway.app

## 2. Migrate schema + data

From a machine with access to your local Postgres:

```bash
# Dump local schema + data
pg_dump --no-owner --no-privileges culture_circle_inventory > cc_dump.sql

# Load into the hosted DB
psql "$DATABASE_URL" < cc_dump.sql
```

(If you only need schema and want to start fresh, run `db/schema.sql` and `db/schema_orders.sql` against the hosted DB instead.)

## 3. Generate Google OAuth env-var values

The `tools/google_sheets.py` flow needs a refresh-token-bearing `token.json`. Generate it once locally:

```bash
cd culture_circle_inventory
venv/bin/python tools/google_sheets.py read 1ruCsGpMN58hiNaJosSezZ-1ykesnRvXKzmaXFYPWApA "'PLAINS STOCK'!A1:B2"
# Sign in via the browser. Token written to ../token.json.
```

Then read both files into env-var-friendly strings:

```bash
cat ../credentials.json | tr -d '\n'   # → GOOGLE_CREDENTIALS_JSON
cat ../token.json       | tr -d '\n'   # → GOOGLE_TOKEN_JSON
```

## 4. Required Vercel env vars

In the Vercel dashboard (Project → Settings → Environment Variables):

```text
# Postgres
DATABASE_URL=postgres://user:pass@host:5432/db?sslmode=require

# Google Sheets sync (paste the raw JSON contents)
GOOGLE_TOKEN_JSON={"token":"...","refresh_token":"...","client_id":"...","client_secret":"...","scopes":["https://www.googleapis.com/auth/spreadsheets"]}
GOOGLE_CREDENTIALS_JSON={"installed":{"client_id":"...","client_secret":"...","redirect_uris":[...]}}

# Flask
SECRET_KEY=<random 32+ char string>

# Shopify (one pair per store)
PIEREERIC_SHOPIFY_DOMAIN=...
PIEREERIC_SHOPIFY_ACCESS_TOKEN=shpat_...
ALICEMEYERS_SHOPIFY_DOMAIN=...
ALICEMEYERS_SHOPIFY_ACCESS_TOKEN=shpat_...
# ... repeat for each prefix in config.SHOPIFY_STORE_PREFIXES
```

## 5. Deploy

```bash
cd culture_circle_inventory
npx vercel link        # one-time
npx vercel --prod      # ship
```

Or push the repo to GitHub and import the project in the Vercel dashboard. Set the **Root Directory** to `culture_circle_inventory`.

Vercel auto-detects `vercel.json`:

```json
{
  "builds":   [{ "src": "api/index.py", "use": "@vercel/python" }],
  "rewrites": [{ "source": "/(.*)", "destination": "/api/index" }]
}
```

Every request hits `api/index.py`, which imports the Flask `app` callable and the runtime hands the WSGI request to it.

## 6. Verify

After deploy, hit the public URL:
- `/` → dashboard
- `/orders/` → Shopify orders list
- `/orders/export.csv?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD` → CSV download
- `POST /blanks/sync-from-sheet` → triggers `tools.sync_inventory_from_sheet.sync()` directly (no subprocess)

## Operational notes

- **Cold starts.** Each invocation opens a fresh Postgres connection. For higher traffic add a connection pooler (Supabase pgbouncer, Neon serverless driver, or PgBouncer in front of Vercel Postgres).
- **Token refresh.** When the Google access token expires, `tools/google_sheets.py` refreshes in-memory using the refresh token from `GOOGLE_TOKEN_JSON`. The refreshed access token is _not_ written back to env (Vercel env is read-only at runtime), but that's fine — refresh tokens stay valid for ~6 months of inactivity. Regenerate `GOOGLE_TOKEN_JSON` if Google revokes it.
- **Cron jobs** (`sync_shopify.py`, `daily_import.py`) → port to Vercel Cron Jobs if you want them. Otherwise run them from any machine that has the same env vars.
