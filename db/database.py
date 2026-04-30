import psycopg2
import psycopg2.extras
from flask import g, current_app


def _open_connection():
    cfg = current_app.config
    url = cfg.get('DATABASE_URL') or ''
    # Pin the Postgres session timezone so CURRENT_DATE / `::date` casts
    # match the user's clock (Vercel runs functions in UTC by default).
    tz = cfg.get('APP_TIMEZONE') or 'Asia/Kolkata'
    options = f"-c TimeZone={tz}"
    if url:
        return psycopg2.connect(url, options=options)
    return psycopg2.connect(
        dbname=cfg['DB_NAME'],
        user=cfg['DB_USER'],
        password=cfg['DB_PASSWORD'],
        host=cfg['DB_HOST'],
        port=cfg['DB_PORT'],
        options=options,
    )


def get_db():
    if 'db' not in g:
        g.db = _open_connection()
        g.db.autocommit = False
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query(sql, params=None, fetch='all'):
    """Execute a SELECT query and return results as list of dicts."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        if fetch == 'one':
            row = cur.fetchone()
            return dict(row) if row else None
        return [dict(r) for r in cur.fetchall()]


def execute(sql, params=None):
    """Execute an INSERT/UPDATE/DELETE and commit."""
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params)
    db.commit()


def execute_returning(sql, params=None):
    """Execute and return the first row (for INSERT ... RETURNING)."""
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    db.commit()
    return dict(row) if row else None


def init_app(app):
    app.config.from_object('config.Config')
    app.teardown_appcontext(close_db)
