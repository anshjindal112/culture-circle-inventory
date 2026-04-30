"""
Google Sheets helper — read/write a Sheet via OAuth.

Credentials & token resolution (in order):
  1. Env vars `GOOGLE_CREDENTIALS_JSON` + `GOOGLE_TOKEN_JSON` — the JSON contents
     of the OAuth client + an authorized user token. Required on Vercel since
     the filesystem is read-only/ephemeral.
  2. Local files at the project root: `credentials.json`, `token.json`. Used
     for dev when running `python tools/google_sheets.py ...` from a terminal.

When the access token expires, we refresh in-memory. Locally we also persist
the refreshed token back to `token.json`. On serverless we don't try to write
back — the refresh token in env keeps minting access tokens on each cold start.

CLI:
    python tools/google_sheets.py read  <spreadsheet_id> <range>
    python tools/google_sheets.py write <spreadsheet_id> <range> <json_values>
    python tools/google_sheets.py append <spreadsheet_id> <range> <json_values>
"""

import json
import os
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Project root: where credentials.json/token.json live in dev.
# This file lives at culture_circle_inventory/tools/google_sheets.py — go up
# two levels to claude_project/ where the OAuth files were placed originally.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "token.json"


def _load_creds():
    """Return google.oauth2.credentials.Credentials, refreshing if needed."""
    token_json = os.environ.get("GOOGLE_TOKEN_JSON")
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    creds = None

    # 1) Token from env — preferred path on Vercel.
    if token_json:
        try:
            info = json.loads(token_json)
            creds = Credentials.from_authorized_user_info(info, SCOPES)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"GOOGLE_TOKEN_JSON is malformed: {exc}") from exc

    # 2) Token from local file — dev path.
    elif TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if creds and creds.valid:
        return creds

    # Try to refresh
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist back locally; on serverless this is a no-op since the file
        # is in a read-only or ephemeral location.
        if not token_json:
            try:
                TOKEN_FILE.write_text(creds.to_json())
            except OSError:
                pass
        return creds

    # No creds yet — kick off the interactive OAuth flow. Only works locally.
    if creds_json:
        flow = InstalledAppFlow.from_client_config(json.loads(creds_json), SCOPES)
    elif CREDENTIALS_FILE.exists():
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    else:
        raise RuntimeError(
            "No Google credentials available. Set GOOGLE_TOKEN_JSON (and "
            "optionally GOOGLE_CREDENTIALS_JSON) env vars on the deploy, or "
            "place credentials.json + token.json at the project root for local "
            "development."
        )
    creds = flow.run_local_server(port=0)
    try:
        TOKEN_FILE.write_text(creds.to_json())
    except OSError:
        pass
    return creds


def get_service():
    return build("sheets", "v4", credentials=_load_creds(), cache_discovery=False)


def read_sheet(spreadsheet_id: str, range_: str):
    service = get_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_)
        .execute()
    )
    values = result.get("values", [])
    print(json.dumps(values, indent=2))
    return values


def write_sheet(spreadsheet_id: str, range_: str, values: list):
    service = get_service()
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption="USER_ENTERED",
            body=body,
        )
        .execute()
    )
    print(f"Updated {result.get('updatedCells')} cells.")
    return result


def append_sheet(spreadsheet_id: str, range_: str, values: list):
    service = get_service()
    body = {"values": values}
    result = (
        service.spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body,
        )
        .execute()
    )
    print(f"Appended {result.get('updates', {}).get('updatedCells')} cells.")
    return result


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]
    spreadsheet_id = sys.argv[2]
    range_ = sys.argv[3]

    if command == "read":
        read_sheet(spreadsheet_id, range_)
    elif command in ("write", "append"):
        if len(sys.argv) < 5:
            print("Error: json_values argument required for write/append")
            sys.exit(1)
        values = json.loads(sys.argv[4])
        if command == "write":
            write_sheet(spreadsheet_id, range_, values)
        else:
            append_sheet(spreadsheet_id, range_, values)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
