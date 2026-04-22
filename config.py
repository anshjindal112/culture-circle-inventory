import os
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'cc-inventory-dev-key')
    DB_NAME = os.environ.get('DB_NAME', 'culture_circle_inventory')
    DB_USER = os.environ.get('DB_USER', 'anshjindal')
    DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
    DB_HOST = os.environ.get('DB_HOST', 'localhost')
    DB_PORT = os.environ.get('DB_PORT', '5432')


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
]


def get_shopify_stores():
    """Return list of configured Shopify stores with credentials."""
    stores = []
    for prefix in SHOPIFY_STORE_PREFIXES:
        token = os.environ.get(f'{prefix}_SHOPIFY_ACCESS_TOKEN')
        domain = os.environ.get(f'{prefix}_SHOPIFY_DOMAIN')
        if token and domain:
            stores.append({
                'prefix': prefix,
                'name': prefix.replace('_', ' ').title(),
                'access_token': token,
                'domain': domain,
            })
    return stores
