import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


load_dotenv()


def fetch_schema() -> dict:
    connection_settings = {
        "host": os.environ["DB_HOST"],
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 10,
    }

    query = """
        SELECT
            c.table_schema,
            c.table_name,
            c.ordinal_position,
            c.column_name,
            c.data_type,
            c.udt_name,
            c.is_nullable,
            c.column_default,
            CASE WHEN tc.constraint_type = 'PRIMARY KEY' THEN true ELSE false END AS is_primary_key
        FROM information_schema.columns c
        LEFT JOIN information_schema.key_column_usage kcu
            ON kcu.table_schema = c.table_schema
            AND kcu.table_name = c.table_name
            AND kcu.column_name = c.column_name
            AND kcu.ordinal_position = c.ordinal_position
        LEFT JOIN information_schema.table_constraints tc
            ON tc.constraint_schema = kcu.constraint_schema
            AND tc.constraint_name = kcu.constraint_name
            AND tc.table_schema = kcu.table_schema
            AND tc.table_name = kcu.table_name
        WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY c.table_schema, c.table_name, c.ordinal_position
    """

    tables: dict[str, dict] = {}
    with psycopg.connect(**connection_settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            for row in cursor.fetchall():
                (
                    schema_name, table_name, ordinal_position, column_name,
                    data_type, udt_name, is_nullable, column_default, is_primary_key,
                ) = row
                table_key = f"{schema_name}.{table_name}"
                table = tables.setdefault(
                    table_key,
                    {"schema": schema_name, "table": table_name, "columns": []},
                )
                table["columns"].append(
                    {
                        "position": ordinal_position,
                        "name": column_name,
                        "data_type": data_type,
                        "internal_type": udt_name,
                        "nullable": is_nullable == "YES",
                        "default": column_default,
                        "primary_key": bool(is_primary_key),
                    }
                )

    return {"database": os.environ["DB_NAME"], "tables": list(tables.values())}


def main() -> int:
    output_path = Path(os.getenv("SCHEMA_OUTPUT", "output/schema.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = fetch_schema()
    output_path.write_text(json.dumps(document, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"Fetched {len(document['tables'])} table(s)")
    print(output_path.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
