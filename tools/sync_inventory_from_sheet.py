#!/usr/bin/env python3
"""
Sync Culture Circle inventory FROM a Google Sheet INTO the Postgres dashboard.

The sheet is the source of truth — this tool reads the sheet and upserts
`blank_master.current_stock` to match. Existing burn-rate / restock data
is left untouched.

Expected sheet schema (wide format):
    Row 1 = header. Columns:
        A: Garment Type    (forward-filled across colour rows)
        B: Color
        C..H: XS, S, M, L, XL, XXL  (quantities)
    Row 2+ = data rows.

Usage:
    python tools/sync_inventory_from_sheet.py
    python tools/sync_inventory_from_sheet.py --sheet-id <id> --range "Sheet1!A1:H"
    python tools/sync_inventory_from_sheet.py --dry-run

Env / defaults:
    SHEET_ID          — defaults to the Culture Circle inventory sheet.
    SHEET_RANGE       — defaults to "'PLAINS STOCK'!A1:H".
    DATABASE_URL      — full Postgres URL; takes precedence over DB_* vars.
    DB_NAME / DB_USER / DB_PASSWORD / DB_HOST / DB_PORT — fallback if no URL.

Importable: `sync(sheet_id, range_, dry_run=False)` returns a summary dict.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

# Importable both as a script and as a module.
HERE = Path(__file__).resolve().parent
APP_DIR = HERE.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from tools.google_sheets import get_service  # noqa: E402

DEFAULT_SHEET_ID = "1ruCsGpMN58hiNaJosSezZ-1ykesnRvXKzmaXFYPWApA"
DEFAULT_RANGE = "'PLAINS STOCK'!A1:H"
SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL"]


def _connect():
    """Open a Postgres connection. Prefers DATABASE_URL, falls back to DB_*."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        dbname=os.environ.get("DB_NAME", "culture_circle_inventory"),
        user=os.environ.get("DB_USER", "anshjindal"),
        password=os.environ.get("DB_PASSWORD", ""),
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
    )


def fetch_rows(sheet_id: str, range_: str):
    service = get_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=range_)
        .execute()
    )
    return result.get("values", [])


def parse_rows(rows):
    """Yield (garment_type, color, size, qty) for each cell in the wide sheet."""
    if not rows:
        return
    header = [c.strip().upper() for c in rows[0]]
    try:
        size_indices = {sz: header.index(sz) for sz in SIZE_ORDER if sz in header}
    except ValueError:
        size_indices = {}
    if not size_indices:
        size_indices = {sz: 2 + i for i, sz in enumerate(SIZE_ORDER)}

    last_garment_type = ""
    for row in rows[1:]:
        if len(row) < 2:
            continue
        garment_type = (row[0] or "").strip()
        if garment_type:
            last_garment_type = garment_type
        else:
            garment_type = last_garment_type
        color = (row[1] or "").strip().upper()
        if not garment_type or not color:
            continue
        for size, col_idx in size_indices.items():
            if col_idx >= len(row):
                qty = 0
            else:
                cell = (row[col_idx] or "").strip()
                try:
                    qty = int(float(cell)) if cell else 0
                except ValueError:
                    qty = 0
            yield garment_type, color, size, qty


def sync(sheet_id: str, range_: str, dry_run: bool = False):
    rows = fetch_rows(sheet_id, range_)
    parsed = list(parse_rows(rows))

    summary = {
        "fetched_rows": max(0, len(rows) - 1),
        "cells": len(parsed),
        "updated": 0,
        "created": 0,
        "unchanged": 0,
        "dry_run": dry_run,
    }

    if not parsed:
        return summary

    if dry_run:
        return summary

    conn = _connect()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    for garment_type, color, size, qty in parsed:
        # Look up by the actual unique key — (blank_name, size) — so we don't
        # miss existing rows when their `garment_type` casing differs from the
        # sheet. The DB stores 'Oversized T-Shirt' (mixed case) while the sheet
        # sends 'OVERSIZED T-SHIRT'; matching on those would always miss and
        # then collide on the unique constraint.
        blank_name = f"{garment_type.upper()} - {color}"
        cur.execute(
            "SELECT blank_id, current_stock FROM blank_master "
            "WHERE blank_name = %s AND size = %s",
            (blank_name, size),
        )
        row = cur.fetchone()
        if row:
            if int(row["current_stock"] or 0) == qty:
                summary["unchanged"] += 1
            else:
                cur.execute(
                    "UPDATE blank_master SET current_stock = %s, updated_at = NOW() "
                    "WHERE blank_id = %s",
                    (qty, row["blank_id"]),
                )
                summary["updated"] += 1
        else:
            cur.execute(
                """
                INSERT INTO blank_master (blank_name, garment_type, color, size, current_stock, lead_time_days)
                VALUES (%s, %s, %s, %s, %s, 21)
                ON CONFLICT (blank_name, size) DO UPDATE SET
                    current_stock = EXCLUDED.current_stock,
                    updated_at = NOW()
                RETURNING (xmax = 0) AS inserted
                """,
                (blank_name, garment_type, color, size, qty),
            )
            res = cur.fetchone()
            if res and res["inserted"]:
                summary["created"] += 1
            else:
                summary["updated"] += 1

    cur.execute(
        "INSERT INTO import_batches (file_name, order_count, imported_at) "
        "VALUES (%s, %s, NOW())",
        (f"google-sheet:{sheet_id}", summary["cells"]),
    )

    conn.commit()
    cur.close()
    conn.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sheet-id", default=os.environ.get("SHEET_ID", DEFAULT_SHEET_ID))
    parser.add_argument("--range", dest="range_", default=os.environ.get("SHEET_RANGE", DEFAULT_RANGE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit summary as JSON only.")
    args = parser.parse_args()

    try:
        summary = sync(args.sheet_id, args.range_, dry_run=args.dry_run)
    except Exception as exc:
        err = {"ok": False, "error": str(exc), "type": type(exc).__name__}
        print(json.dumps(err))
        sys.exit(2)

    summary["ok"] = True
    if args.json:
        print(json.dumps(summary))
    else:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
