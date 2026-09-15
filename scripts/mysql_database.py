"""Check MySQL or explicitly create an empty database; never copy local data."""
import argparse
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_config import load_local_configuration
from mysql_store import mysql_options


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--database")
    parser.add_argument("--create-database", action="store_true", help="Create database if absent, then initialize tables")
    args = parser.parse_args()
    load_local_configuration(args.env_file)
    for key in ("host", "port", "database"):
        if getattr(args, key) is not None:
            os.environ["CHESTNUT_MYSQL_" + key.upper()] = str(getattr(args, key))
    import pymysql
    options = mysql_options()
    database = options.pop("database")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", database):
        raise ValueError("Database name must contain only ASCII letters, digits and underscores")
    connection = pymysql.connect(**options)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            print("Connection OK; MySQL version:", cursor.fetchone()[0])
            if args.create_database:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
            cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (database,))
            exists = cursor.fetchone() is not None
            print("Business database:", database, "exists" if exists else "not created")
        if args.create_database:
            from mysql_store import MySQLAdminStore
            store = MySQLAdminStore(dict(options, database=database))
            store.close()
            print("Schema initialized. No local data imported; administrator password remains unchanged.")
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Drivers may include account names or SQL parameters in their message.
        code = error.args[0] if error.args and isinstance(error.args[0], int) else "n/a"
        print(f"MySQL check failed: {type(error).__name__}, code={code}. Check configuration/network/permissions.", file=sys.stderr)
        raise SystemExit(1)
