"""Shared PostgreSQL connection for Wikipedia ingest staging tables."""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", os.getenv("MARIADB_HOST", "localhost")),
    "port": int(os.getenv("POSTGRES_PORT", os.getenv("MARIADB_PORT", "5433"))),
    "user": os.getenv("POSTGRES_USER", os.getenv("MARIADB_USER", "travel")),
    "password": os.getenv(
        "POSTGRES_PASSWORD", os.getenv("MARIADB_PASSWORD", "changeme")
    ),
    "dbname": os.getenv(
        "POSTGRES_DB", os.getenv("MARIADB_DATABASE", "travel")
    ),
}


def connect(*, row_factory=None):
    kwargs = dict(DB_CONFIG)
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    return psycopg.connect(**kwargs)


def connect_dict():
    return connect(row_factory=dict_row)
