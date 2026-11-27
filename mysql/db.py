"""Load project configuration and provide shared MySQL connections."""

import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from pymysql.cursors import DictCursor


# Load the project .env file, with process environment values taking priority.
# Read password text literally, including any dollar signs.
load_dotenv(
    Path(__file__).resolve().parents[1] / ".env",
    override=False,
    interpolate=False,
)


def get_connection() -> pymysql.connections.Connection:
    """Open a connection to the database configured in .env."""
    database = os.environ.get("MYSQL_DATABASE", "").strip()
    if not database:
        raise ValueError("MYSQL_DATABASE must be set in .env.")

    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=database,
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
        # Business operations explicitly commit or roll back their transactions.
        autocommit=False,
        init_command="SET time_zone = '+00:00'",
    )
