from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import sqlglot
from sqlglot import exp

from app.services.analytics_catalog import CATALOG, allowed_columns

ALLOWLIST_VIEWS = frozenset(CATALOG)
_MAX_SQL_CHARS = 10_000
_DANGEROUS_FUNCTIONS = frozenset(
    {"benchmark", "get_lock", "is_free_lock", "load_file", "master_pos_wait", "sleep"}
)
_ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "avg",
        "coalesce",
        "count",
        "date_format",
        "max",
        "min",
        "round",
        "sum",
    }
)


@dataclass(frozen=True)
class SqlGuardResult:
    allowed: bool
    sql: str
    reason: str | None = None
    tables: tuple[str, ...] = field(default_factory=tuple)
    columns: tuple[str, ...] = field(default_factory=tuple)


def _reject(sql: str, reason: str, *, tables: tuple[str, ...] = ()) -> SqlGuardResult:
    return SqlGuardResult(False, sql, reason, tables)


def _literal_int(node: exp.Expression | None) -> int | None:
    if isinstance(node, exp.Literal) and not node.is_string:
        try:
            return int(node.this)
        except (TypeError, ValueError):
            return None
    return None


def _literal_date(node: exp.Expression | None) -> date | None:
    if not isinstance(node, exp.Literal) or not node.is_string:
        return None
    try:
        parsed = date.fromisoformat(str(node.this))
    except ValueError:
        return None
    return parsed if parsed.isoformat() == str(node.this) else None


def _bounded_date_window(tree: exp.Select, column_name: str) -> tuple[date, date] | None:
    windows: list[tuple[date, date]] = []
    for between in tree.find_all(exp.Between):
        column = between.this
        if not isinstance(column, exp.Column) or str(column.name).lower() != column_name:
            continue
        if between.find_ancestor(exp.Where) is None or between.find_ancestor(exp.Not):
            continue
        start = _literal_date(between.args.get("low"))
        end = _literal_date(between.args.get("high"))
        if start is not None and end is not None:
            windows.append((start, end))
    return windows[0] if len(windows) == 1 else None


def _contains_forbidden_star(tree: exp.Select) -> bool:
    for star in tree.find_all(exp.Star):
        parent = star.parent
        if isinstance(parent, exp.Count) and parent.this is star:
            continue
        return True
    return any(str(column.name) == "*" for column in tree.find_all(exp.Column))


def _function_name(function: exp.Func) -> str:
    if isinstance(function, exp.Anonymous):
        return str(function.name).lower()
    return str(function.sql_name()).lower()


def _source_column_names(tree: exp.Select) -> tuple[str, ...]:
    projection_aliases = {
        str(expression.alias).lower()
        for expression in tree.expressions
        if isinstance(expression, exp.Alias) and expression.alias
    }
    names: set[str] = set()
    for column in tree.find_all(exp.Column):
        name = str(column.name).lower()
        # MySQL permits a SELECT alias in ORDER BY. Such a reference names the
        # projected value, not another source column from the semantic view.
        if name in projection_aliases and column.find_ancestor(exp.Order) is not None:
            continue
        names.add(name)
    return tuple(sorted(names))


def validate_sql(
    sql: str,
    *,
    max_days: int = 90,
    max_rows: int = 200,
    max_columns: int = 20,
    expected_view: str | None = None,
    expected_start_date: date | None = None,
    expected_end_date: date | None = None,
) -> SqlGuardResult:
    text = str(sql or "").strip()
    if not text:
        return _reject(text, "SQL_EMPTY")
    if len(text) > _MAX_SQL_CHARS:
        return _reject(text, "SQL_TOO_LONG")
    try:
        statements = [statement for statement in sqlglot.parse(text, read="mysql") if statement]
    except Exception:
        return _reject(text, "SQL_AST_PARSE_FAILED")
    if len(statements) != 1:
        return _reject(text, "SQL_MULTI_STATEMENT")
    tree = statements[0]
    if not isinstance(tree, exp.Select):
        return _reject(text, "SQL_NOT_SELECT")
    if _contains_forbidden_star(tree):
        return _reject(text, "SQL_STAR_FORBIDDEN")
    if tree.find(exp.Join):
        return _reject(text, "SQL_JOIN_FORBIDDEN")
    if tree.find(exp.Subquery):
        return _reject(text, "SQL_SUBQUERY_FORBIDDEN")
    if tree.find(exp.Lock):
        return _reject(text, "SQL_LOCK_FORBIDDEN")
    if tree.find(exp.Parameter) or tree.find(exp.Var):
        return _reject(text, "SQL_VARIABLE_FORBIDDEN")
    # Session/global variables expose connection or server metadata and are not
    # semantic-view columns. ``@@version`` is parsed as SessionParameter rather
    # than Var, so checking only Var would leave a metadata escape hatch.
    if tree.find(exp.SessionParameter):
        return _reject(text, "SQL_VARIABLE_FORBIDDEN")
    if tree.find(exp.Or):
        return _reject(text, "SQL_OR_FORBIDDEN")
    with_clause = tree.args.get("with_")
    ctes = list(tree.find_all(exp.CTE))
    if len(ctes) > 1 or (with_clause is not None and with_clause.args.get("recursive")):
        return _reject(text, "SQL_CTE_FORBIDDEN")
    if any(column.table for column in tree.find_all(exp.Column)):
        return _reject(text, "SQL_QUALIFIED_COLUMN_FORBIDDEN")
    for function in tree.find_all(exp.Func):
        function_name = _function_name(function)
        if function_name in _DANGEROUS_FUNCTIONS:
            return _reject(text, "SQL_DANGEROUS_FUNCTION")
        if function_name not in _ALLOWED_FUNCTIONS:
            return _reject(text, "SQL_FUNCTION_NOT_ALLOWLISTED")

    if any(table.db or table.catalog for table in tree.find_all(exp.Table)):
        return _reject(text, "SQL_CROSS_DATABASE_FORBIDDEN")

    cte_names = {
        str(cte.alias_or_name).lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
    }
    tables = tuple(
        sorted(
            {
                str(table.name).lower()
                for table in tree.find_all(exp.Table)
                if str(table.name).lower() not in cte_names
            }
        )
    )
    if len(tables) != 1 or tables[0] not in ALLOWLIST_VIEWS:
        return _reject(text, "SQL_VIEW_NOT_ALLOWLISTED", tables=tables)
    if expected_view and tables[0] != str(expected_view).lower():
        return _reject(text, "SQL_PLAN_VIEW_MISMATCH", tables=tables)

    limit = tree.args.get("limit")
    limit_value = _literal_int(limit.expression if isinstance(limit, exp.Limit) else None)
    if limit_value is None:
        return _reject(text, "SQL_LIMIT_REQUIRED", tables=tables)
    if limit_value < 1 or limit_value > max_rows:
        return _reject(text, "SQL_LIMIT_EXCEEDED", tables=tables)
    if tree.args.get("offset") is not None:
        return _reject(text, "SQL_OFFSET_FORBIDDEN", tables=tables)

    selects = list(tree.find_all(exp.Select))
    if any(len(select.expressions) > max_columns for select in selects):
        return _reject(text, "SQL_COLUMN_LIMIT_EXCEEDED", tables=tables)
    view_columns = allowed_columns(tables[0])
    columns = _source_column_names(tree)
    # Projection aliases are output labels, never proof that a source column is
    # part of the semantic contract. Subtracting aliases here allowed
    # ``SELECT 1 AS email, email`` to smuggle an unknown source column through.
    unknown = set(columns) - view_columns
    if unknown:
        return _reject(text, "SQL_COLUMN_NOT_ALLOWLISTED", tables=tables)

    view_contract = CATALOG[tables[0]]
    if view_contract.get("requires_date_filter"):
        window = _bounded_date_window(tree, str(view_contract["date_column"]).lower())
        if window is None:
            return _reject(text, "SQL_DATE_RANGE_REQUIRED", tables=tables)
        start, end = window
        if end < start or (end - start).days + 1 > max_days:
            return _reject(text, "SQL_DATE_RANGE_EXCEEDED", tables=tables)
        if expected_start_date is not None and start != expected_start_date:
            return _reject(text, "SQL_PLAN_DATE_MISMATCH", tables=tables)
        if expected_end_date is not None and end != expected_end_date:
            return _reject(text, "SQL_PLAN_DATE_MISMATCH", tables=tables)
    return SqlGuardResult(True, tree.sql(dialect="mysql"), tables=tables, columns=columns)
