# Project Handoff Context

This document is the entry point for anyone (human or AI) picking up the Culture Circle inventory dashboard. It captures **what was built, why, what state it's in, and where to look** when extending it. Read this before changing code.

## TL;DR

- **What:** Order operations dashboard for the Culture Circle multi-brand network. Pulls Shopify orders from 14 connected stores, exports filtered orders as CSV for daily fulfilment, and syncs blank inventory from a Google Sheet (sheet is the source of truth).
- **Stack:** Flask 3 + Jinja templates + Postgres (psycopg2) + Bootstrap 5 + Bootstrap Icons + Instrument Serif/Inter/JetBrains Mono. No JS framework — everything is server-rendered HTML.
- **Deploy:** Vercel (Python serverless via `@vercel/python`), Neon Postgres, GitHub repo `anshjindal112/culture-circle-inventory` set to auto-deploy `main`. Production URL is whatever Vercel assigned (check the Vercel dashboard).
- **Source of truth for inventory:** Google Sheet `1ruCsGpMN58hiNaJosSezZ-1ykesnRvXKzmaXFYPWApA` (tab `'PLAINS STOCK'`). Sheet → DB sync is one-way; the dashboard never writes to the sheet.

## Repo layout

```
culture_circle_inventory/        ← this is the repo root
├── api/index.py                 ← Vercel serverless entry — exposes Flask `app`
├── vercel.json                  ← rewrites /(.*) → /api/index
├── .vercelignore                ← excludes venv, .env, secrets
├── .env.example                 ← every var the app reads at runtime
├── DEPLOY_VERCEL.md             ← step-by-step Vercel deploy guide
├── README.md                    ← top-level project doc
├── CONTEXT.md                   ← (this file)
├── requirements.txt             ← pinned Python deps incl. google-* libs
├── app.py                       ← Flask factory, registers all blueprints
├── config.py                    ← env var reading + Shopify store registry
├── db/
│   ├── database.py              ← psycopg2 helpers, sets session timezone
│   ├── schema.sql               ← core inventory schema
│   ├── schema_orders.sql        ← Shopify orders schema
│   └── init_db.py
├── routes/
│   ├── dashboard.py             ← landing page (orders-focused)
│   ├── orders.py                ← Shopify orders list + date filter + /export.csv
│   ├── inventory_sync.py        ← POST /blanks/sync-from-sheet
│   ├── blanks.py, restock.py, csv_import.py, sales.py
├── services/
│   ├── shopify_service.py       ← Shopify REST client (orders, fulfillments)
│   └── auto_mapper.py
├── tools/
│   ├── google_sheets.py         ← reads creds/token from env vars (file fallback)
│   └── sync_inventory_from_sheet.py  ← exposes `sync()`; supports DATABASE_URL
├── templates/                   ← Jinja templates, dark-luxe theme
└── static/css/                  ← (empty — all styles live inline in base.html)
```

## Architecture in three lines

1. Browser → Vercel rewrite `/(.*)` → `api/index.py` (which imports the Flask `app`) → Flask routes dispatch to a blueprint → blueprint queries Postgres / calls Shopify / reads Google Sheet → renders Jinja template.
2. **Inventory sync** runs in-process (the Flask route imports `tools.sync_inventory_from_sheet.sync()` directly — **no subprocess** because Vercel serverless can't spawn one reliably).
3. **Shopify orders** live in Postgres tables (`shopify_orders`, `shopify_order_items`, `shopify_fulfillments`); they're refreshed by hitting "Sync All Stores" on `/orders/`, which calls Shopify REST APIs and upserts.

## How environment variables flow

| Var | Read by | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `config.py` → `db/database.py` | Postgres connection. Pooled URL on Vercel (Neon's `-pooler` host); unpooled fine for migrations. |
| `SECRET_KEY` | Flask | Session signing. Just needs to be a long random string. |
| `APP_TIMEZONE` | `config.py` → both `db/database.py` and route handlers | Default `Asia/Kolkata`. Drives both the Postgres session `TimeZone` and Python's "today" calc. |
| `GOOGLE_TOKEN_JSON` | `tools/google_sheets.py` | Authorized-user OAuth token JSON (single line). Refreshes in-memory; we never write back. |
| `GOOGLE_CREDENTIALS_JSON` | `tools/google_sheets.py` | OAuth client JSON. Only needed if a fresh login flow runs (which doesn't happen on Vercel). |
| `<PREFIX>_SHOPIFY_ACCESS_TOKEN` + `<PREFIX>_SHOPIFY_DOMAIN` | `config.get_shopify_stores()` | One pair per store. Prefix list lives in `SHOPIFY_STORE_PREFIXES`. |

**Important Vercel quirk:** env var names can't start with a digit. The store prefix `24SONGS` is therefore aliased — see `ENV_PREFIX_ALIASES` in `config.py`. The DB still uses `24SONGS` as the `store_prefix`; only env-var lookups use `SONGS24`. If you add another digit-starting prefix, add it to that dict.

## What was built (chronological highlights)

The app started as a CSV-import-driven inventory tracker. It was rebuilt around a different model:

1. **UI rebuilt as dark-luxe theme** — `templates/base.html` defines the design system: rich near-black surfaces, warm-gold accent (`#d4af72` → `#e8c994`), Instrument Serif for hero/display, Inter for UI, JetBrains Mono for numerics, subtle SVG noise grain. Shared classes (`.cc-card`, `.stat-card`, `.btn-cc`, `.badge-status`, `.status-pill`) are reused across all templates so any new page inherits the look automatically.
2. **Dashboard replaced** — `templates/dashboard.html` is now an orders-focused landing: editorial hero with 14-day order sparkline, bento-grid stat tiles (Unfulfilled / Today / Fulfilled / Stores), recent-unfulfilled order rows with calendar-style date boxes, store rail, and a gold-trimmed "Sync from Sheet" card. The corresponding query in `routes/dashboard.py` includes a `sparkline` series (last 14 days of order counts) and per-store unfulfilled breakdown.
3. **Inventory sidebar removed** — Blanks / SKU Mappings / Restocks are no longer in the sidebar (Sheet replaced CSV import). The route files still exist on disk for future revival; only the UI surface was hidden.
4. **Sheet → DB sync** — `tools/sync_inventory_from_sheet.py` reads the `'PLAINS STOCK'` tab (wide format: `Garment Type | Color | XS | S | M | L | XL | XXL`, with garment names forward-filled across colour rows) and upserts `blank_master.current_stock`. Lookup is by `(blank_name, size)` (the actual unique key) with `ON CONFLICT DO UPDATE` so casing drift between sheet and DB doesn't trip the unique constraint. Records an `import_batches` row so the dashboard's staleness checks still work.
5. **Orders date filter + CSV export** — `routes/orders.py` accepts `date_from` / `date_to` query params (validated as `YYYY-MM-DD`, applied to `shopify_created_at::date`). `_build_orders_filter()` is shared between `list_orders` and `export_csv`. The export streams one row per **line item** with shipping address, phone, total, note — usable by whoever is packing. Filename includes the active filters and a timestamp.
6. **Vercel-ready** — `api/index.py` exposes the WSGI app; `tools/google_sheets.py` reads creds/token from env vars (with file fallback for local dev); `db/database.py` and `tools/sync_inventory_from_sheet.py` both accept `DATABASE_URL`; the Flask route imports `sync()` directly (no subprocess).
7. **Timezone fix** — Vercel functions run in UTC. `APP_TIMEZONE` (default `Asia/Kolkata`) is now used in both the Postgres connection (`-c TimeZone=...` so `CURRENT_DATE` matches) and in route handlers (`zoneinfo.ZoneInfo(...)` instead of `date.today()`).

## Known gotchas & things to double-check before shipping changes

- **Cold-start latency.** Each Vercel invocation opens a fresh Postgres connection. Acceptable for a small team but adds ~150–300ms. If traffic grows, switch to Neon's serverless driver or add a real connection pooler.
- **OAuth token refresh.** `GOOGLE_TOKEN_JSON` holds a refresh token. We refresh in-memory and **never write back** (Vercel's filesystem is read-only). If Google ever revokes it (e.g., 6 months unused), regenerate locally with `python tools/google_sheets.py read <sheet-id> "'PLAINS STOCK'!A1:B2"` and update the env var.
- **Sheet schema drift.** The sync expects exactly two text columns (Garment Type, Color) followed by six numeric columns (XS–XXL). Header row is detected case-insensitively; non-numeric cells become 0. If the sheet structure changes, `parse_rows()` in `tools/sync_inventory_from_sheet.py` is the only thing that needs updating.
- **24SONGS prefix.** See above — env vars use `SONGS24_*`, DB rows still use `24SONGS`. Don't "fix" the DB; the alias is intentional.
- **Subprocess is forbidden.** Anything that needs to run a tool from the web request must `import` it. Don't `subprocess.run(...)` — it'll either silently no-op or hit Vercel's 60s function limit.
- **`.vercelignore` ≠ `.gitignore`.** Both exclude `venv/`, secrets, etc., but `.vercelignore` runs at deploy time (decides what's in the function bundle); `.gitignore` runs at commit time. Update both if you add a new "shouldn't be in production" file.
- **Database session TZ.** All `CURRENT_DATE` / `::date` casts in SQL assume the session timezone is set. If you write a new SQL query that compares dates, you can rely on `db/database.py` having pinned the session to `Asia/Kolkata` already.

## How to run locally

```bash
cd culture_circle_inventory
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in real values
python app.py          # http://localhost:5002
```

For the Sheet sync, drop `credentials.json` + `token.json` in the project root (one level above `culture_circle_inventory/` if mirroring the original layout, or set the env vars). On first run the sync will use those files; on Vercel it uses env vars.

## How to extend (common future asks)

| If you want to... | Edit |
| --- | --- |
| Add a new page | New blueprint in `routes/`; register it in `app.py`; new template under `templates/`. The shared base template already gives you the dark theme. |
| Tweak the dashboard widget set | `routes/dashboard.py` (queries) + `templates/dashboard.html` (rendering). Sparkline data lives in `sparkline` context var. |
| Change the Sheet schema or default range | `tools/sync_inventory_from_sheet.py` — top-level constants + `parse_rows()`. |
| Add another Shopify store | Append the prefix to `SHOPIFY_STORE_PREFIXES` in `config.py`; if the prefix starts with a digit, add it to `ENV_PREFIX_ALIASES` too. Then add the two env vars on Vercel. |
| Change the order export format | `routes/orders.py:export_csv()` — the `writer.writerow([...])` calls. |
| Add user auth | None of the current code assumes any. The user has a half-built mental model around Google SSO + roles (admin / operator / viewer) — see the chat history if continuing this thread. |

## What's not built (yet)

- **Auth.** Anyone with the URL has full access. The user has discussed adding Google SSO with role-based access; nothing committed yet.
- **Cron-driven Shopify sync.** Currently triggered by clicking "Sync All Stores" on `/orders/`. The script `sync_shopify.py` exists for cron but nothing is scheduled on Vercel — would need Vercel Cron Jobs.
- **Sheet-write-back.** Sheet → DB only. If the team starts editing inventory in the dashboard (currently they don't, since the inventory pages are hidden), we'd need DB → Sheet sync.
- **Per-store access control.** The orders list shows every store to every viewer.
- **`24SONGS` migration.** Long-term, renaming `24SONGS` → `SONGS24` everywhere (including in DB rows) would simplify the alias hack. Not urgent.

## Where to look when something breaks

| Symptom | Likely cause |
| --- | --- |
| Every page returns 500 | `DATABASE_URL` env var missing/wrong on Vercel, or Neon paused (Neon free tier auto-suspends inactive instances; first request after wakes it up after a few seconds). |
| `/blanks/sync-from-sheet` flashes "Google OAuth refresh token is invalid" | `GOOGLE_TOKEN_JSON` expired. Regenerate locally and update the env var. |
| Orders page is empty but Shopify has orders | Click "Sync All Stores" once. If still empty, check Shopify env vars on Vercel for any store with `403 Forbidden` errors (their API key needs `read_orders` scope). |
| "Today" shows yesterday's date | `APP_TIMEZONE` got unset somehow (it defaults to `Asia/Kolkata` though, so this should be rare). Check Vercel env vars. |
| Sync says "0 cells parsed" | The sheet's tab name or range changed. Check `DEFAULT_RANGE` in `tools/sync_inventory_from_sheet.py`. |

## Key external resources

- **GitHub repo:** https://github.com/anshjindal112/culture-circle-inventory
- **Inventory sheet:** https://docs.google.com/spreadsheets/d/1ruCsGpMN58hiNaJosSezZ-1ykesnRvXKzmaXFYPWApA/edit
- **Vercel dashboard:** project name `culture-circle-inventory` under team `anshjindal112-1131s-projects`
- **Neon database:** project `neon-cerise-bridge` (US-East-1)

That's the whole picture. When in doubt, the README and DEPLOY_VERCEL.md cover the original setup; this file covers the design intent and edge cases.
