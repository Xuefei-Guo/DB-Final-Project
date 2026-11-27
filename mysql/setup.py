"""Initialize the project database by executing schema.sql."""

import os
from pathlib import Path
import sys

import pymysql
from dotenv import load_dotenv
from pymysql.constants import CLIENT


def main() -> None:
    mysql_dir = Path(__file__).resolve().parent
    # Load local settings and read the SQL file from their project locations.
    load_dotenv(mysql_dir.parent / ".env", interpolate=False)
    schema_sql = (mysql_dir / "schema.sql").read_text(encoding="utf-8")

    # Connect to the server so schema.sql can create and select the database.
    with pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        charset="utf8mb4",
        autocommit=True,
        client_flag=CLIENT.MULTI_STATEMENTS,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema_sql)
            # Read every statement result so SQL errors are reported.
            while cursor.nextset():
                pass

    print("Schema applied successfully.")


if __name__ == "__main__":
    try:
        main()
    except (pymysql.MySQLError, OSError, ValueError, KeyError) as error:
        print(f"Schema setup failed. {error}", file=sys.stderr)
        sys.exit(1)
