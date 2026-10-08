import os
import sys

import psycopg
from dotenv import load_dotenv


load_dotenv()


def main() -> int:
    required = {
        "host": os.getenv("DB_HOST"),
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME"),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"Missing environment variable(s): {', '.join('DB_' + name.upper() for name in missing)}")
        return 2

    try:
        with psycopg.connect(
            host=required["host"],
            port=required["port"],
            dbname=required["dbname"],
            user=required["user"],
            password=required["password"],
            connect_timeout=10,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user, now()")
                database, user, server_time = cursor.fetchone()
        print("Database connection successful")
        print(f"Database: {database}")
        print(f"User: {user}")
        print(f"Server time: {server_time}")
        return 0
    except Exception as exc:
        print(f"Database connection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
