import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


load_dotenv()


def main() -> int:
    output_path = Path(os.getenv("GSTFETCH_SCHEMA_OUTPUT", "output/gstfetch_schema.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    query = """
        SELECT table_name, column_name, data_type, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'gstfetch'
        ORDER BY table_name, ordinal_position
    """

    tables: dict[str, list[dict[str, str]]] = {}
    with psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            for table_name, column_name, data_type, udt_name in cursor.fetchall():
                tables.setdefault(table_name, []).append(
                    {
                        "column": column_name,
                        "type": data_type,
                        "internal_type": udt_name,
                    }
                )

    result = {"schema": "gstfetch", "tables": tables}
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Found {len(tables)} table(s) in schema gstfetch")
    for table_name, columns in tables.items():
        print(f"\n{table_name}")
        for column in columns:
            print(f"  {column['column']}: {column['type']}")
    print(f"\nSaved JSON to: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
