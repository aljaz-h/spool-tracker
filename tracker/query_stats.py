"""Settings -> Server "Query performance" - what Postgres itself knows
about which queries are slow, from the pg_stat_statements extension (the
slowest query shapes by total or mean time, how often they run, how much of
their data came from cache) and from pg_stat_user_tables (tables read by
sequential scan far more than by index - the usual sign of a missing index).

Every function takes the Django `connection` so it can be exercised with a
stand-in in tests; on SQLite (the dev fallback) there is nothing to read and
status() says so. Queries come back from Postgres already normalised
($1, $2 placeholders - no literal values - but still the app's own SQL, so
the panel is owner-only."""

import logging
import re

from django.db import DatabaseError

logger = logging.getLogger(__name__)

# state of the extension, in the order a problem would be fixed
ERROR = "error"  # Postgres refused one of the stats queries (permissions, version, ...)
UNSUPPORTED = "unsupported"  # not Postgres
NOT_PRELOADED = "not_preloaded"  # shared_preload_libraries does not list it - needs a DB restart
NO_EXTENSION = "no_extension"  # preloaded, but CREATE EXTENSION has not run
READY = "ready"

SORTS = {
    "total": ("total_exec_time", "Total time"),
    "mean": ("mean_exec_time", "Slowest on average"),
    "calls": ("calls", "Most often"),
}
DEFAULT_SORT = "total"

# A query averaging over this many ms is flagged in the panel.
SLOW_MEAN_MS = 100
# A table is only suspect for seq scans once it has this many rows.
SEQ_SCAN_MIN_ROWS = 1000


_SELECT_LIST = re.compile(r"^(SELECT(?:\s+DISTINCT)?)\s+(.*?)\s+(FROM\s.*)$", re.I | re.S)
_BREAK_BEFORE = re.compile(
    r"\s+(FROM|INNER JOIN|LEFT OUTER JOIN|LEFT JOIN|RIGHT JOIN|CROSS JOIN|WHERE|GROUP BY|HAVING|ORDER BY|LIMIT|OFFSET|UNION ALL|UNION|SET|VALUES|RETURNING)\s",
    re.I,
)
_SUMMARY_LENGTH = 170
# A column list shorter than this is kept in the summary (SELECT COUNT(*) ...).
_SHORT_COLUMN_LIST = 60


def summarize(query, limit=_SUMMARY_LENGTH):
    """One readable line for a query: whitespace and identifier quotes
    removed and a long SELECT column list (Django selects every column of
    every joined table) collapsed to an ellipsis - what is left is the
    tables, joins and filters, which is what you read to judge a query."""
    text = " ".join(query.split()).replace('"', "")
    match = _SELECT_LIST.match(text)
    if match and len(match.group(2)) > _SHORT_COLUMN_LIST:
        text = f"{match.group(1)} \u2026 {match.group(3)}"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def format_sql(query):
    """The full statement over several lines (a break before each clause,
    one selected column per line) for the expanded view and for copying."""
    text = " ".join(query.split())
    match = _SELECT_LIST.match(text)
    if match:
        columns = re.sub(r",\s*", ",\n  ", match.group(2))
        text = f"{match.group(1)}\n  {columns}\n{match.group(3)}"
    text = _BREAK_BEFORE.sub(lambda m: f"\n{m.group(1).upper() if m.group(1).islower() else m.group(1)} ", text)
    return re.sub(r"\s+(AND|OR)\s", lambda m: f"\n  {m.group(1)} ", text)


def clean_sort(value):
    return value if value in SORTS else DEFAULT_SORT


def _is_postgres(connection):
    return getattr(connection, "vendor", "") == "postgresql"


def status(connection):
    """One of UNSUPPORTED / NOT_PRELOADED / NO_EXTENSION / READY."""
    if not _is_postgres(connection):
        return UNSUPPORTED
    with connection.cursor() as cursor:
        cursor.execute("SHOW shared_preload_libraries")
        preloaded = (cursor.fetchone() or [""])[0]
        if "pg_stat_statements" not in preloaded:
            return NOT_PRELOADED
        cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'")
        if cursor.fetchone() is None:
            return NO_EXTENSION
    return READY


def _hit_pct(hit, read):
    total = (hit or 0) + (read or 0)
    return None if not total else round((hit or 0) / total * 100)


def top_queries(connection, sort=DEFAULT_SORT, limit=15):
    """The slowest query shapes for this database, best-first by `sort`
    (a key of SORTS - anything else falls back to the default, so it is
    never interpolated from user input). Our own stats queries are left out."""
    column = SORTS[clean_sort(sort)][0]
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT query, calls, total_exec_time, mean_exec_time, max_exec_time, rows,
                   shared_blks_hit, shared_blks_read
            FROM pg_stat_statements
            WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
              AND query NOT ILIKE '%%pg_stat_statements%%'
              AND query NOT ILIKE '%%pg_stat_user_tables%%'
              AND query NOT ILIKE 'SHOW %%'
            ORDER BY {column} DESC
            LIMIT %s
            """,
            [int(limit)],
        )
        rows = cursor.fetchall()
    result = []
    for query, calls, total_ms, mean_ms, max_ms, row_count, hit, read in rows:
        result.append(
            {
                "query": " ".join(query.split()),
                "summary": summarize(query),
                "sql": format_sql(query),
                "calls": calls,
                "total_ms": round(total_ms or 0),
                "mean_ms": round(mean_ms or 0, 1),
                "max_ms": round(max_ms or 0),
                "rows": row_count,
                "cache_hit_pct": _hit_pct(hit, read),
                "slow": (mean_ms or 0) >= SLOW_MEAN_MS,
            }
        )
    return result


def table_scans(connection, limit=8):
    """Tables Postgres read by sequential scan more often than by index,
    biggest rows-read first - the likely candidates for a missing index.
    Small tables are ignored (a seq scan of a few hundred rows is correct)."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT relname, seq_scan, seq_tup_read, COALESCE(idx_scan, 0), n_live_tup
            FROM pg_stat_user_tables
            WHERE n_live_tup >= %s AND seq_scan > COALESCE(idx_scan, 0)
            ORDER BY seq_tup_read DESC
            LIMIT %s
            """,
            [SEQ_SCAN_MIN_ROWS, int(limit)],
        )
        rows = cursor.fetchall()
    return [
        {"table": name, "seq_scans": seq, "rows_read": tup_read, "index_scans": idx, "rows": live}
        for name, seq, tup_read, idx, live in rows
    ]


def reset(connection):
    """Clears the collected statistics (pg_stat_statements_reset) so the next
    window starts clean - useful right after a fix, to see if it helped."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_statements_reset()")


def snapshot(connection, sort=DEFAULT_SORT):
    """Everything the panel shows, in one dict."""
    context = {"state": UNSUPPORTED, "sort": clean_sort(sort), "sorts": SORTS, "slow_ms": SLOW_MEAN_MS}
    try:
        context["state"] = status(connection)
        if context["state"] == READY:
            context["queries"] = top_queries(connection, sort)
            context["table_scans"] = table_scans(connection)
    except DatabaseError:
        # A panel that explains itself beats an empty box - and the real
        # reason (a missing privilege, an old Postgres) is in the log.
        logger.warning("Could not read Postgres query statistics", exc_info=True)
        context["state"] = ERROR
    return context
