"""
Vercel serverless entry. Vercel routes every request to this module via the
`vercel.json` rewrite, and the platform's Python runtime invokes the WSGI
`app` callable that Flask exports.
"""

import sys
from pathlib import Path

# Make the app package importable regardless of where Vercel mounts the file.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import app  # noqa: E402,F401  (Vercel discovers this `app` symbol)
