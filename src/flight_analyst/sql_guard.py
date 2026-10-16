"""Reject analyst SQL that is not a single read-only query."""

from __future__ import annotations

import re

ALLOWED_TABLES = {
    "flights",
    "flights_v",
    "carriers",
    "airports",
    "carrier_families",
    "metadata",
}

FORBIDDEN_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "copy",
    "attach",
    "detach",
    "export",
    "import",
    "pragma",
    "install",
    "load",
    "call",
    "grant",
    "revoke",
    "truncate",
    "replace",
    "vacuum",
    "checkpoint",
    "set",
    "reset",
    "begin",
    "commit",
    "rollback",
}

FORBIDDEN_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_blob",
    "read_text",
    "parquet_scan",
    "sqlite_scan",
    "postgres_scan",
    "mysql_scan",
    "iceberg_scan",
    "delta_scan",
    "load_extension",
    "glob",
    "query",
}


class SqlGuardError(ValueError):
    pass


def _strip_comments_and_strings(sql: str) -> str:
    out = []
    i = 0
    n = len(sql)
    while i < n:
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        if sql[i] in {"'", '"'} or sql.startswith("$$", i):
            if sql.startswith("$$", i):
                end = sql.find("$$", i + 2)
                i = n if end < 0 else end + 2
            else:
                quote = sql[i]
                i += 1
                while i < n:
                    if sql[i] == quote:
                        if i + 1 < n and sql[i + 1] == quote:
                            i += 2
                            continue
                        i += 1
                        break
                    i += 1
            out.append(" ")
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


def prepare_sql(sql: str) -> str:
    """Normalize and validate a model-written statement. Return the SQL to run."""
    if sql is None or not str(sql).strip():
        raise SqlGuardError("SQL is empty.")
    text = str(sql).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    while text.endswith(";"):
        text = text[:-1].rstrip()
    if not text:
        raise SqlGuardError("SQL is empty.")
    scrubbed = _strip_comments_and_strings(text)
    if ";" in scrubbed:
        raise SqlGuardError("Only one SQL statement is allowed.")
    if not re.match(r"^(select|with)\b", scrubbed.strip(), flags=re.IGNORECASE):
        raise SqlGuardError("SQL must start with SELECT or WITH.")
    for match in re.finditer(r"\b([a-zA-Z_][\w]*)\b", scrubbed):
        word = match.group(1).lower()
        if word in FORBIDDEN_KEYWORDS:
            raise SqlGuardError(f"SQL contains forbidden keyword {word}.")
        if word in FORBIDDEN_FUNCTIONS:
            raise SqlGuardError(f"SQL contains forbidden function {word}.")
        cte_names = {
            match.group(1).lower()
            for match in re.finditer(
                r"(?:\bwith\b|,)\s*([a-zA-Z_][\w]*)\s+as\s*\(",
                scrubbed,
                flags=re.IGNORECASE,
            )
        }
        for match in re.finditer(r"\b(?:from|join)\s+([a-zA-Z_][\w]*)", scrubbed, flags=re.IGNORECASE):
            table = match.group(1).lower()
            if table not in ALLOWED_TABLES and table not in cte_names:
                raise SqlGuardError(f"SQL references table {table}, which is not allowed.")
        return text
