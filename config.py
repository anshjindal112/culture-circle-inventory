import os
from dotenv import load_dotenv

# Load .env from the project root if present (no-op on Vercel where env vars
# come from the dashboard).
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'cc-inventory-dev-key')

    # Hosted providers (Vercel Postgres / Neon / Supabase) ship a single
    # connection string. Fall back to per-field vars for local dev.
    DATABASE_URL = os.environ.get('DATABASE_URL', '')
    DB_NAME = os.environ.get('DB_NAME', 'culture_circle_inventory')
    DB_USER = os.environ.get('DB_USER', 'anshjindal')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '5432')

    # Display + SQL "today" calculations use this timezone. Vercel runs
    # functions in UTC; Culture Circle is India-based, so default IST.
    APP_TIMEZONE = os.environ.get('APP_TIMEZONE', 'Asia/Kolkata')


# All Shopify store prefixes from .env
SHOPIFY_STORE_PREFIXES = [
    'PIEREERIC',
    'ALICEMEYERS',
    'KAAND',
    'VOYD',
    'COMOATELIER',
    '24SONGS',
    'CITYOFDOMES',
    'BLACKLISTCO',
    'SMILINGCAT',
    'OFF_SUPPLY',
    'FORFKSAKE',
    'MYUGEN',
    'ALANKOCH',
    'BE_AUTYST',
    'GYMBRAT',
]


# Vercel/POSIX env var names can't start with a digit. For prefixes that do,
# we look up the env vars under a digit-free alias while keeping the original
# prefix as the in-code/DB identifier (so existing Shopify orders stay tied
# to their store).
ENV_PREFIX_ALIASES = {
    '24SONGS': 'SONGS24',
}


def _env_prefix(prefix: str) -> str:
    return ENV_PREFIX_ALIASES.get(prefix, prefix)


def get_shopify_stores():
    """Return list of configured Shopify stores with credentials."""
    stores = []
    for prefix in SHOPIFY_STORE_PREFIXES:
        env_prefix = _env_prefix(prefix)
        token = os.environ.get(f'{env_prefix}_SHOPIFY_ACCESS_TOKEN')
        domain = os.environ.get(f'{env_prefix}_SHOPIFY_DOMAIN')
        if token and domain:
            stores.append({
                'prefix': prefix,
                'name': prefix.replace('_', ' ').title(),
                'access_token': token,
                'domain': domain,
            })
    return stores
