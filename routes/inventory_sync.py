"""
Inventory sync route — pull inventory from the Culture Circle Google Sheet.

The sheet is the source of truth. On Vercel we can't subprocess into a venv,
so we import the sync function directly. Errors are caught and surfaced as
flash messages, with a friendlier message for an expired OAuth refresh token.
"""

from flask import Blueprint, redirect, url_for, flash, request

from tools.sync_inventory_from_sheet import sync, DEFAULT_SHEET_ID, DEFAULT_RANGE

inventory_sync_bp = Blueprint('inventory_sync', __name__, url_prefix='/blanks')


@inventory_sync_bp.route('/sync-from-sheet', methods=['POST'])
def sync_from_sheet():
    """Pull current inventory from the Google Sheet into blank_master."""
    sheet_id = (request.form.get('sheet_id') or DEFAULT_SHEET_ID).strip()
    range_ = (request.form.get('range') or DEFAULT_RANGE).strip()

    try:
        result = sync(sheet_id, range_)
    except Exception as exc:
        msg = str(exc)
        if 'invalid_grant' in msg or 'RefreshError' in type(exc).__name__:
            flash(
                "Google OAuth refresh token is invalid. Generate a fresh "
                "token.json locally (`python tools/google_sheets.py read "
                "<sheet-id> \"Sheet1!A1:B2\"`) and update the "
                "GOOGLE_TOKEN_JSON env var on the deploy.",
                'danger',
            )
        else:
            flash(f"Sync failed: {msg}", 'danger')
        return redirect(url_for('dashboard.index'))

    if result.get('cells', 0) == 0:
        flash('Sheet had no parseable rows. Check the sheet name / range.', 'warning')
    else:
        flash(
            f"Synced {result['cells']} rows from Google Sheet — "
            f"{result['updated']} updated, {result['created']} created, "
            f"{result['unchanged']} unchanged.",
            'success',
        )
    return redirect(url_for('dashboard.index'))
