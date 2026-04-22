"""
Initialize the database with all schemas.

Usage:
    python db/init_db.py
"""
import psycopg2
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

DB_NAME = os.environ.get('DB_NAME', 'culture_circle_inventory')
DB_USER = os.environ.get('DB_USER', 'anshjindal')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')
DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')

SCHEMA_DIR = os.path.dirname(__file__)


def create_database():
    conn = psycopg2.connect(dbname='postgres', user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE "{DB_NAME}"')
        print(f"Created database: {DB_NAME}")
    else:
        print(f"Database {DB_NAME} already exists")
    cur.close()
    conn.close()


def run_schemas():
    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
    conn.autocommit = True
    cur = conn.cursor()

    # Run schemas in order
    schema_files = ['schema.sql', 'schema_orders.sql']
    for sf in schema_files:
        path = os.path.join(SCHEMA_DIR, sf)
        if os.path.exists(path):
            with open(path) as f:
                cur.execute(f.read())
            print(f"  Applied {sf}")
        else:
            print(f"  Skipped {sf} (not found)")

    cur.close()
    conn.close()
    print("Schema setup complete")


if __name__ == '__main__':
    create_database()
    run_schemas()
    print("Done! Database ready.")
