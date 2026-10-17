from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from ..models import (
    ColumnInfo,
    ConnectionConfig,
    ForeignKeyInfo,
    ScannedSchema,
    TableInfo,
)

CATEGORICAL_HINTS = (
    "status", "state", "level", "channel", "category", "sub_category", "type",
    "city", "province", "region", "pay_method", "gender", "source", "stage",
    "tier", "segment", "brand", "currency",
)

LAKE_SOURCES = {"trino", "databricks", "athena", "duckdb", "clickhouse", "bigquery", "spark"}
CONNECTOR_SOURCES = LAKE_SOURCES | {"oracle", "mssql"}


def _quote(dialect: str, name: str) -> str:
    if dialect == "postgres":
        return '"' + name.replace('"', '""') + '"'
    return "`" + name.replace("`", "``") + "`"


def _qualified(dialect: str, schema: str, table: str) -> str:
    return f"{_quote(dialect, schema)}.{_quote(dialect, table)}"


def _stringify(value: Any, max_len: int = 60) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    text = str(value).strip().replace("\n", " ")
    return text[:max_len]


def md_type(dialect: str, data_type: str, column_type: str = "") -> str:
    dt = (data_type or "").lower()
    ct = (column_type or "").lower()
    if dialect == "postgres":
        if dt in ("integer", "int4", "smallint", "int2", "serial", "smallserial"):
            return "INTEGER"
        if dt in ("bigint", "int8", "bigserial"):
            return "BIGINT"
        if dt in ("numeric", "decimal", "money"):
            return "DECIMAL"
        if dt in ("real", "float4", "double precision", "float8"):
            return "DOUBLE"
        if dt == "boolean":
            return "BOOLEAN"
        if dt == "date":
            return "DATE"
        if dt.startswith("timestamp"):
            return "TIMESTAMP"
        if dt.startswith("time"):
            return "TIME"
        if dt in ("json", "jsonb"):
            return "JSON"
        return "VARCHAR"
    # MySQL
    if ct.startswith("tinyint(1)") or dt == "bit":
        return "BOOLEAN"
    if dt in ("tinyint", "smallint", "mediumint", "int", "integer", "year"):
        return "INTEGER"
    if dt == "bigint":
        return "BIGINT"
    if dt in ("decimal", "numeric"):
        return "DECIMAL"
    if dt in ("float", "double", "real"):
        return "DOUBLE"
    if dt == "date":
        return "DATE"
    if dt in ("datetime", "timestamp"):
        return "TIMESTAMP"
    if dt == "time":
        return "TIME"
    if dt == "json":
        return "JSON"
    if dt == "boolean":
        return "BOOLEAN"
    return "VARCHAR"


def infer_role(name: str, mdl_type: str, is_key: bool) -> str:
    lower = name.lower()
    if is_key or lower == "id" or lower.endswith("_id"):
        return "key"
    if mdl_type in ("DATE", "TIMESTAMP", "TIME") or re.search(r"(date|time|_at|month|year|week|day)$", lower):
        return "time"
    if mdl_type in ("INTEGER", "BIGINT", "DECIMAL", "DOUBLE"):
        if re.search(r"(amount|revenue|price|cost|fee|total|qty|quantity|count|num|discount|profit|sales|gmv|salary|score|rate|balance|tax|shipping)", lower):
            return "measure"
        return "measure"
    return "dimension"


def _sample_rows(
    dialect: str, schema: str, table: str, kwargs: dict[str, Any], limit: int = 3
) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {_qualified(dialect, schema, table)} LIMIT {limit}"
    try:
        if dialect == "postgres":
            import psycopg

            with psycopg.connect(**kwargs, row_factory=psycopg.rows.dict_row) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = 12000")
                    cur.execute(sql)
                    return list(cur.fetchall())
        else:
            import MySQLdb
            import MySQLdb.cursors

            conn_kwargs = dict(kwargs)
            conn_kwargs.pop("cursorclass", None)
            with MySQLdb.connect(**conn_kwargs, cursorclass=MySQLdb.cursors.DictCursor) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET SESSION MAX_EXECUTION_TIME = 12000")
                    cur.execute(sql)
                    return list(cur.fetchall())
    except Exception:
        return []


def _distinct_values(
    dialect: str, schema: str, table: str, column: str,
    kwargs: dict[str, Any], limit: int = 12,
) -> list[str]:
    col = _quote(dialect, column)
    sql = (
        f"SELECT {col} AS v, COUNT(*) AS c FROM {_qualified(dialect, schema, table)} "
        f"WHERE {col} IS NOT NULL GROUP BY {col} ORDER BY c DESC LIMIT {limit}"
    )
    try:
        if dialect == "postgres":
            import psycopg

            with psycopg.connect(**kwargs) as conn:
                with conn.cursor() as cur:
                    cur.execute("SET statement_timeout = 12000")
                    cur.execute(sql)
                    return [_stringify(row[0]) for row in cur.fetchall()]
        import MySQLdb

        conn_kwargs = dict(kwargs)
        conn_kwargs.pop("cursorclass", None)
        with MySQLdb.connect(**conn_kwargs) as conn:
            with conn.cursor() as cur:
                cur.execute("SET SESSION MAX_EXECUTION_TIME = 12000")
                cur.execute(sql)
                return [_stringify(row[0]) for row in cur.fetchall()]
    except Exception:
        return []


def test_connection(conn: ConnectionConfig) -> dict[str, Any]:
    from ..vector.base import VECTOR_SOURCES

    if conn.dialect in VECTOR_SOURCES:
        from ..vector.factory import get_adapter

        adapter = get_adapter(conn)
        try:
            return adapter.test()
        finally:
            adapter.close()

    if conn.dialect == "postgres":
        import psycopg

        with psycopg.connect(
            host=conn.host, port=conn.port, dbname=conn.database,
            user=conn.user, password=conn.password, connect_timeout=8,
        ) as db:
            with db.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]
        return {"ok": True, "dialect": conn.dialect, "version": version}
    if conn.dialect == "mysql":
        import MySQLdb

        with MySQLdb.connect(
            host=conn.host, port=conn.port, user=conn.user, passwd=conn.password,
            db=conn.database, charset="utf8mb4", connect_timeout=8,
        ) as db:
            with db.cursor() as cur:
                cur.execute("SELECT VERSION()")
                version = cur.fetchone()[0]
        return {"ok": True, "dialect": conn.dialect, "version": version}

    if conn.dialect in CONNECTOR_SOURCES:
        connector = _wren_connector(conn)
        try:
            sql = "SELECT 1 AS ok FROM DUAL" if conn.dialect == "oracle" else "SELECT 1 AS ok"
            rows = _query_dicts(connector, sql)
            return {
                "ok": True,
                "dialect": conn.dialect,
                "version": f"{conn.dialect} connection ok",
                "sample": rows[0] if rows else None,
            }
        finally:
            _close_connector(connector)

    raise ValueError(f"Unsupported data source: {conn.dialect}")


def scan_schema(conn: ConnectionConfig) -> ScannedSchema:
    if conn.dialect == "postgres":
        return _scan_postgres(conn)
    if conn.dialect == "mysql":
        return _scan_mysql(conn)
    if conn.dialect == "oracle":
        return _scan_oracle(conn)
    if conn.dialect == "mssql":
        return _scan_mssql(conn)
    if conn.dialect in LAKE_SOURCES:
        return _scan_lake(conn)
    raise ValueError(f"Unsupported data source: {conn.dialect}")


def _scan_postgres(conn: ConnectionConfig) -> ScannedSchema:
    import psycopg

    schema = conn.schema_name or "public"
    kwargs = {
        "host": conn.host, "port": conn.port, "dbname": conn.database,
        "user": conn.user, "password": conn.password, "connect_timeout": 8,
    }
    tables: dict[str, TableInfo] = {}
    with psycopg.connect(**kwargs) as db:
        with db.cursor() as cur:
            cur.execute("SET statement_timeout = 20000")
            cur.execute(
                """
                SELECT c.relname,
                       obj_description(c.oid, 'pg_class') AS table_comment,
                       GREATEST(c.reltuples::bigint, 0) AS row_estimate
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind IN ('r', 'p') AND n.nspname = %s
                ORDER BY c.relname
                """,
                (schema,),
            )
            for name, comment, rows in cur.fetchall():
                tables[name] = TableInfo(
                    name=name, schema_name=schema, comment=comment, row_count=int(rows or 0)
                )

            cur.execute(
                """
                SELECT table_name, column_name, data_type, udt_name, is_nullable,
                       column_default, ordinal_position,
                       col_description((quote_ident(table_schema) || '.' || quote_ident(table_name))::regclass::oid,
                                       ordinal_position) AS column_comment
                FROM information_schema.columns
                WHERE table_schema = %s
                ORDER BY table_name, ordinal_position
                """,
                (schema,),
            )
            for (table, column, data_type, udt, nullable, default, ordinal, comment) in cur.fetchall():
                if table not in tables:
                    continue
                tables[table].columns.append(
                    ColumnInfo(
                        name=column,
                        data_type=udt or data_type,
                        mdl_type=md_type("postgres", data_type, udt),
                        nullable=(nullable == "YES"),
                        default=str(default) if default is not None else None,
                        comment=comment,
                        ordinal=int(ordinal),
                    )
                )

            cur.execute(
                """
                SELECT tc.table_name, tc.constraint_name, tc.constraint_type,
                       kcu.column_name, ccu.table_name AS ref_table, ccu.column_name AS ref_column,
                       kcu.ordinal_position
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_name = tc.constraint_name
                 AND kcu.constraint_schema = tc.constraint_schema
                LEFT JOIN information_schema.constraint_column_usage ccu
                  ON ccu.constraint_name = tc.constraint_name
                 AND ccu.constraint_schema = tc.constraint_schema
                WHERE tc.table_schema = %s
                  AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
                ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position
                """,
                (schema,),
            )
            fk_groups: dict[tuple[str, str], ForeignKeyInfo] = {}
            for (table, cname, ctype, column, ref_table, ref_column, ordinal) in cur.fetchall():
                if table not in tables:
                    continue
                if ctype == "PRIMARY KEY":
                    tables[table].primary_key.append(column)
                    for col in tables[table].columns:
                        if col.name == column:
                            col.is_primary_key = True
                            col.role = "key"
                elif ctype == "FOREIGN KEY" and ref_table:
                    key = (table, cname)
                    if key not in fk_groups:
                        fk_groups[key] = ForeignKeyInfo(
                            name=cname, columns=[], ref_table=ref_table, ref_columns=[]
                        )
                    fk_groups[key].columns.append(column)
                    fk_groups[key].ref_columns.append(ref_column or "")
                    for col in tables[table].columns:
                        if col.name == column:
                            col.is_foreign_key = True
                            col.role = "key"
            for (table, _), fk in fk_groups.items():
                tables[table].foreign_keys.append(fk)

    for table in tables.values():
        # Refine roles now that keys are known.
        for col in table.columns:
            col.role = infer_role(col.name, col.mdl_type, col.is_primary_key or col.is_foreign_key)
        samples = _sample_rows("postgres", schema, table.name, kwargs, 3)
        _fill_sample_values("postgres", schema, table, samples, kwargs)
    return ScannedSchema(
        dialect="postgres", database=conn.database, schema_name=schema,
        tables=list(tables.values()),
    )


def _scan_mysql(conn: ConnectionConfig) -> ScannedSchema:
    import MySQLdb
    import MySQLdb.cursors

    schema = conn.schema_name or conn.database
    kwargs = {
        "host": conn.host, "port": conn.port, "user": conn.user,
        "passwd": conn.password, "db": conn.database, "charset": "utf8mb4",
        "connect_timeout": 8, "cursorclass": MySQLdb.cursors.DictCursor,
    }
    tables: dict[str, TableInfo] = {}
    with MySQLdb.connect(**kwargs) as db:
        with db.cursor() as cur:
            cur.execute(
                """
                SELECT TABLE_NAME AS table_name, TABLE_COMMENT AS table_comment,
                       TABLE_ROWS AS table_rows
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'
                ORDER BY TABLE_NAME
                """,
                (schema,),
            )
            for row in cur.fetchall():
                tables[row["table_name"]] = TableInfo(
                    name=row["table_name"],
                    schema_name=schema,
                    comment=row["table_comment"] or None,
                    row_count=int(row["table_rows"] or 0),
                )

            cur.execute(
                """
                SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name,
                       DATA_TYPE AS data_type, COLUMN_TYPE AS column_type,
                       IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default,
                       ORDINAL_POSITION AS ordinal, COLUMN_KEY AS column_key,
                       COLUMN_COMMENT AS column_comment
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_NAME, ORDINAL_POSITION
                """,
                (schema,),
            )
            for row in cur.fetchall():
                table = row["table_name"]
                if table not in tables:
                    continue
                tables[table].columns.append(
                    ColumnInfo(
                        name=row["column_name"],
                        data_type=row["column_type"] or row["data_type"],
                        mdl_type=md_type("mysql", row["data_type"], row["column_type"]),
                        nullable=(row["is_nullable"] == "YES"),
                        default=str(row["column_default"]) if row["column_default"] is not None else None,
                        is_primary_key=(row["column_key"] == "PRI"),
                        comment=row["column_comment"] or None,
                        ordinal=int(row["ordinal"]),
                    )
                )
                if row["column_key"] == "PRI":
                    tables[table].primary_key.append(row["column_name"])

            cur.execute(
                """
                SELECT TABLE_NAME AS table_name, CONSTRAINT_NAME AS constraint_name,
                       COLUMN_NAME AS column_name, REFERENCED_TABLE_NAME AS ref_table,
                       REFERENCED_COLUMN_NAME AS ref_column, ORDINAL_POSITION AS ordinal
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL
                ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
                """,
                (schema,),
            )
            fk_groups: dict[tuple[str, str], ForeignKeyInfo] = {}
            for row in cur.fetchall():
                table = row["table_name"]
                if table not in tables:
                    continue
                key = (table, row["constraint_name"])
                if key not in fk_groups:
                    fk_groups[key] = ForeignKeyInfo(
                        name=row["constraint_name"], columns=[],
                        ref_table=row["ref_table"], ref_columns=[],
                    )
                fk_groups[key].columns.append(row["column_name"])
                fk_groups[key].ref_columns.append(row["ref_column"] or "")
                for col in tables[table].columns:
                    if col.name == row["column_name"]:
                        col.is_foreign_key = True
            for (table, _), fk in fk_groups.items():
                tables[table].foreign_keys.append(fk)

    for table in tables.values():
        for col in table.columns:
            col.role = infer_role(col.name, col.mdl_type, col.is_primary_key or col.is_foreign_key)
        samples = _sample_rows("mysql", schema, table.name, kwargs, 3)
        _fill_sample_values("mysql", schema, table, samples, kwargs)
        if table.row_count == 0 or table.row_count < 100000:
            # information_schema.TABLE_ROWS is an InnoDB estimate and can be
            # badly stale. Exact-count small/medium tables; keep the estimate
            # for large tables to avoid a full scan during onboarding.
            try:
                conn_kwargs = dict(kwargs)
                conn_kwargs.pop("cursorclass", None)
                with MySQLdb.connect(**conn_kwargs) as count_db:
                    with count_db.cursor() as cur:
                        cur.execute("SET SESSION MAX_EXECUTION_TIME = 12000")
                        cur.execute(f"SELECT COUNT(*) FROM {_qualified('mysql', schema, table.name)}")
                        table.row_count = int(cur.fetchone()[0])
                        table.row_count_estimated = False
            except Exception:
                pass
    return ScannedSchema(
        dialect="mysql", database=conn.database, schema_name=schema,
        tables=list(tables.values()),
    )


def _fill_sample_values(
    dialect: str, schema: str, table: TableInfo, samples: list[dict[str, Any]],
    kwargs: dict[str, Any],
) -> None:
    for col in table.columns:
        values: list[str] = []
        for row in samples:
            raw = row.get(col.name)
            text = _stringify(raw)
            if text and text not in values:
                values.append(text)
        low = col.name.lower()
        if col.mdl_type == "VARCHAR" and any(hint in low for hint in CATEGORICAL_HINTS):
            extra = _distinct_values(dialect, schema, table.name, col.name, kwargs)
            for value in extra:
                if value and value not in values:
                    values.append(value)
        col.sample_values = values[:12]

# ---------------------------------------------------------------------------
# Oracle and SQL Server introspection (through Wren's own connectors).
# ---------------------------------------------------------------------------


def _row_value(row: dict[str, Any], key: str) -> Any:
    if key in row:
        return row[key]
    lower = key.lower()
    for candidate, value in row.items():
        if str(candidate).lower() == lower:
            return value
    return None


def _quote_mssql(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _qualified_mssql(schema: str, table: str) -> str:
    return f"{_quote_mssql(schema)}.{_quote_mssql(table)}"


def _oracle_md_type(data_type: str, precision: Any = None, scale: Any = None) -> str:
    dt = (data_type or "").lower()
    if dt == "number":
        if precision is None:
            return "DECIMAL"
        p = int(precision or 0)
        s = int(scale or 0)
        if s == 0:
            if p <= 4:
                return "INTEGER"
            if p <= 18:
                return "BIGINT"
        return "DECIMAL"
    if dt in ("binary_float", "binary_double", "float"):
        return "DOUBLE"
    if dt in ("varchar2", "nvarchar2", "char", "nchar", "clob", "nclob", "long", "rowid", "urowid", "xmltype"):
        return "VARCHAR"
    if dt == "date":
        return "TIMESTAMP"
    if dt.startswith("timestamp"):
        return "TIMESTAMP"
    if dt in ("blob", "raw", "long raw"):
        return "VARCHAR"
    if dt == "boolean":
        return "BOOLEAN"
    if dt in ("json", "jsonb"):
        return "JSON"
    return "VARCHAR"


def _mssql_md_type(data_type: str) -> str:
    dt = (data_type or "").lower()
    if dt in ("int", "smallint", "tinyint"):
        return "INTEGER"
    if dt == "bigint":
        return "BIGINT"
    if dt in ("decimal", "numeric", "money", "smallmoney"):
        return "DECIMAL"
    if dt in ("float", "real"):
        return "DOUBLE"
    if dt == "bit":
        return "BOOLEAN"
    if dt == "date":
        return "DATE"
    if dt in ("datetime", "datetime2", "smalldatetime", "datetimeoffset"):
        return "TIMESTAMP"
    if dt == "time":
        return "TIME"
    if dt == "json":
        return "JSON"
    return "VARCHAR"


def _scan_oracle(conn: ConnectionConfig) -> ScannedSchema:
    connector = _wren_connector(conn)
    schema = (conn.schema_name or conn.user or "").upper()
    try:
        try:
            table_rows = _query_dicts(
                connector,
                "SELECT t.table_name AS table_name, c.comments AS table_comment, t.num_rows AS row_count "
                "FROM all_tables t "
                "LEFT JOIN all_tab_comments c ON c.owner = t.owner AND c.table_name = t.table_name "
                f"WHERE t.owner = {_sql_str(schema)} AND t.nested = 'NO' ORDER BY t.table_name",
            )
        except Exception:
            table_rows = _query_dicts(
                connector,
                "SELECT table_name AS table_name, NULL AS table_comment, num_rows AS row_count "
                f"FROM all_tables WHERE owner = {_sql_str(schema)} ORDER BY table_name",
            )
        tables: dict[str, TableInfo] = {}
        for row in table_rows:
            name = _row_value(row, "table_name")
            if not name:
                continue
            tables[str(name)] = TableInfo(
                name=str(name),
                schema_name=schema,
                catalog=None,
                comment=_row_value(row, "table_comment"),
                row_count=int(_row_value(row, "row_count") or 0),
                row_count_estimated=True,
                table_type="TABLE",
            )
        try:
            column_rows = _query_dicts(
                connector,
                "SELECT c.table_name AS table_name, c.column_name AS column_name, "
                "c.data_type AS data_type, c.data_precision AS data_precision, c.data_scale AS data_scale, "
                "c.nullable AS is_nullable, c.column_id AS ordinal_position, "
                "c.data_default AS column_default, cc.comments AS column_comment "
                "FROM all_tab_columns c "
                "LEFT JOIN all_col_comments cc ON cc.owner = c.owner "
                "AND cc.table_name = c.table_name AND cc.column_name = c.column_name "
                f"WHERE c.owner = {_sql_str(schema)} ORDER BY c.table_name, c.column_id",
            )
        except Exception:
            column_rows = _query_dicts(
                connector,
                "SELECT table_name AS table_name, column_name AS column_name, data_type AS data_type, "
                "data_precision AS data_precision, data_scale AS data_scale, nullable AS is_nullable, "
                "column_id AS ordinal_position, data_default AS column_default "
                f"FROM all_tab_columns WHERE owner = {_sql_str(schema)} "
                "ORDER BY table_name, column_id",
            )
        for row in column_rows:
            table = tables.get(str(_row_value(row, "table_name")))
            if not table:
                continue
            data_type = str(_row_value(row, "data_type") or "")
            table.columns.append(
                ColumnInfo(
                    name=str(_row_value(row, "column_name")),
                    data_type=data_type,
                    mdl_type=_oracle_md_type(
                        data_type,
                        _row_value(row, "data_precision"),
                        _row_value(row, "data_scale"),
                    ),
                    nullable=(str(_row_value(row, "is_nullable") or "").upper() == "Y"),
                    default=(
                        str(_row_value(row, "column_default"))
                        if _row_value(row, "column_default") is not None
                        else None
                    ),
                    comment=_row_value(row, "column_comment"),
                    ordinal=int(_row_value(row, "ordinal_position") or 0),
                )
            )
        try:
            constraint_rows = _query_dicts(
                connector,
                "SELECT ac.table_name AS table_name, ac.constraint_name AS constraint_name, "
                "ac.constraint_type AS constraint_type, acc.column_name AS column_name, "
                "rc.table_name AS ref_table, rcc.column_name AS ref_column, acc.position AS ordinal "
                "FROM all_constraints ac "
                "JOIN all_cons_columns acc ON acc.owner = ac.owner AND acc.constraint_name = ac.constraint_name "
                "LEFT JOIN all_constraints rc ON rc.owner = ac.r_owner AND rc.constraint_name = ac.r_constraint_name "
                "LEFT JOIN all_cons_columns rcc ON rcc.owner = rc.owner "
                "AND rcc.constraint_name = rc.constraint_name AND rcc.position = acc.position "
                f"WHERE ac.owner = {_sql_str(schema)} AND ac.constraint_type IN ('P', 'R') "
                "ORDER BY ac.table_name, ac.constraint_name, acc.position",
            )
        except Exception:
            constraint_rows = []
        fk_groups: dict[tuple[str, str], ForeignKeyInfo] = {}
        for row in constraint_rows:
            table = tables.get(str(_row_value(row, "table_name")))
            if not table:
                continue
            column = str(_row_value(row, "column_name"))
            ctype = str(_row_value(row, "constraint_type") or "").upper()
            if ctype == "P":
                table.primary_key.append(column)
                for col in table.columns:
                    if col.name == column:
                        col.is_primary_key = True
                        col.role = "key"
            elif ctype == "R":
                key = (table.name, str(_row_value(row, "constraint_name")))
                ref_table = _row_value(row, "ref_table")
                if not ref_table:
                    continue
                if key not in fk_groups:
                    fk_groups[key] = ForeignKeyInfo(
                        name=key[1], columns=[], ref_table=str(ref_table), ref_columns=[]
                    )
                fk_groups[key].columns.append(column)
                fk_groups[key].ref_columns.append(str(_row_value(row, "ref_column") or ""))
                for col in table.columns:
                    if col.name == column:
                        col.is_foreign_key = True
                        col.role = "key"
        for (table_name, _), fk in fk_groups.items():
            if table_name in tables:
                tables[table_name].foreign_keys.append(fk)

        for table in tables.values():
            _finalize_columns(table)
            _apply_pk_heuristic(table)
            if table.row_count == 0:
                try:
                    count_rows = _query_dicts(
                        connector,
                        f'SELECT COUNT(*) AS n FROM "{schema}"."{table.name}"',
                    )
                    table.row_count = int(_row_value(count_rows[0], "n") or 0) if count_rows else 0
                    table.row_count_estimated = False
                except Exception:
                    pass
            sample_sql = f'SELECT * FROM "{schema}"."{table.name}" FETCH FIRST 3 ROWS ONLY'
            try:
                samples = _query_dicts(connector, sample_sql)
            except Exception:
                try:
                    samples = _query_dicts(
                        connector,
                        f'SELECT * FROM "{schema}"."{table.name}" WHERE ROWNUM <= 3',
                    )
                except Exception:
                    samples = []
            _fill_samples_lake(connector, "oracle", table, samples)
        return ScannedSchema(
            dialect="oracle",
            database=conn.database,
            catalog=None,
            schema_name=schema,
            tables=list(tables.values()),
        )
    finally:
        _close_connector(connector)


def _scan_mssql(conn: ConnectionConfig) -> ScannedSchema:
    connector = _wren_connector(conn)
    schema = conn.schema_name or "dbo"
    try:
        table_rows = _query_dicts(
            connector,
            "SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name, TABLE_TYPE AS table_type "
            "FROM INFORMATION_SCHEMA.TABLES "
            f"WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = {_sql_str(schema)} "
            "ORDER BY TABLE_NAME",
        )
        tables: dict[str, TableInfo] = {}
        for row in table_rows:
            name = str(_row_value(row, "table_name"))
            tables[name] = TableInfo(
                name=name,
                schema_name=str(_row_value(row, "schema_name") or schema),
                catalog=None,
                table_type="TABLE",
            )
        row_counts: dict[tuple[str, str], int] = {}
        try:
            for row in _query_dicts(
                connector,
                "SELECT OBJECT_SCHEMA_NAME(p.object_id) AS schema_name, "
                "OBJECT_NAME(p.object_id) AS table_name, SUM(p.rows) AS row_count "
                "FROM sys.partitions p "
                "WHERE p.index_id IN (0, 1) AND OBJECTPROPERTY(p.object_id, 'IsUserTable') = 1 "
                "GROUP BY p.object_id",
            ):
                key = (
                    str(_row_value(row, "schema_name") or "").lower(),
                    str(_row_value(row, "table_name") or "").lower(),
                )
                row_counts[key] = int(_row_value(row, "row_count") or 0)
        except Exception:
            pass
        for table in tables.values():
            table.row_count = row_counts.get((table.schema_name.lower(), table.name.lower()), 0)
            table.row_count_estimated = True
        try:
            column_rows = _query_dicts(
                connector,
                "SELECT TABLE_SCHEMA AS schema_name, TABLE_NAME AS table_name, "
                "COLUMN_NAME AS column_name, DATA_TYPE AS data_type, "
                "IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default, "
                "ORDINAL_POSITION AS ordinal_position "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                f"WHERE TABLE_SCHEMA = {_sql_str(schema)} "
                "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            )
        except Exception:
            column_rows = []
        for row in column_rows:
            table = tables.get(str(_row_value(row, "table_name")))
            if not table:
                continue
            data_type = str(_row_value(row, "data_type") or "")
            table.columns.append(
                ColumnInfo(
                    name=str(_row_value(row, "column_name")),
                    data_type=data_type,
                    mdl_type=_mssql_md_type(data_type),
                    nullable=(str(_row_value(row, "is_nullable") or "").upper() == "YES"),
                    default=(
                        str(_row_value(row, "column_default"))
                        if _row_value(row, "column_default") is not None
                        else None
                    ),
                    ordinal=int(_row_value(row, "ordinal_position") or 0),
                )
            )
        try:
            comment_rows = _query_dicts(
                connector,
                "SELECT s.name AS schema_name, t.name AS table_name, ep.value AS comment "
                "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
                "LEFT JOIN sys.extended_properties ep ON ep.major_id = t.object_id "
                "AND ep.minor_id = 0 AND ep.name = 'MS_Description' "
                "WHERE ep.value IS NOT NULL",
            )
            for row in comment_rows:
                table = tables.get(str(_row_value(row, "table_name")))
                if table:
                    table.comment = _row_value(row, "comment")
        except Exception:
            pass
        try:
            constraint_rows = _query_dicts(
                connector,
                "SELECT tc.TABLE_SCHEMA AS schema_name, tc.TABLE_NAME AS table_name, "
                "tc.CONSTRAINT_NAME AS constraint_name, tc.CONSTRAINT_TYPE AS constraint_type, "
                "kcu.COLUMN_NAME AS column_name, ccu.TABLE_NAME AS ref_table, "
                "ccu.COLUMN_NAME AS ref_column, kcu.ORDINAL_POSITION AS ordinal "
                "FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc "
                "JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu "
                "ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME AND kcu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA "
                "LEFT JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu "
                "ON ccu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME AND ccu.CONSTRAINT_SCHEMA = tc.CONSTRAINT_SCHEMA "
                f"WHERE tc.TABLE_SCHEMA = {_sql_str(schema)} "
                "AND tc.CONSTRAINT_TYPE IN ('PRIMARY KEY', 'FOREIGN KEY') "
                "ORDER BY tc.TABLE_NAME, tc.CONSTRAINT_NAME, kcu.ORDINAL_POSITION",
            )
        except Exception:
            constraint_rows = []
        fk_groups: dict[tuple[str, str], ForeignKeyInfo] = {}
        for row in constraint_rows:
            table = tables.get(str(_row_value(row, "table_name")))
            if not table:
                continue
            column = str(_row_value(row, "column_name"))
            ctype = str(_row_value(row, "constraint_type") or "").upper()
            if ctype == "PRIMARY KEY":
                table.primary_key.append(column)
                for col in table.columns:
                    if col.name == column:
                        col.is_primary_key = True
                        col.role = "key"
            else:
                ref_table = _row_value(row, "ref_table")
                if not ref_table:
                    continue
                key = (table.name, str(_row_value(row, "constraint_name")))
                if key not in fk_groups:
                    fk_groups[key] = ForeignKeyInfo(
                        name=key[1], columns=[], ref_table=str(ref_table), ref_columns=[]
                    )
                fk_groups[key].columns.append(column)
                fk_groups[key].ref_columns.append(str(_row_value(row, "ref_column") or ""))
                for col in table.columns:
                    if col.name == column:
                        col.is_foreign_key = True
                        col.role = "key"
        for (table_name, _), fk in fk_groups.items():
            if table_name in tables:
                tables[table_name].foreign_keys.append(fk)

        for table in tables.values():
            _finalize_columns(table)
            _apply_pk_heuristic(table)
            try:
                samples = _query_dicts(
                    connector,
                    f"SELECT TOP 3 * FROM {_qualified_mssql(table.schema_name, table.name)}",
                )
            except Exception:
                samples = []
            _fill_samples_lake(connector, "mssql", table, samples)
        return ScannedSchema(
            dialect="mssql",
            database=conn.database,
            catalog=None,
            schema_name=schema,
            tables=list(tables.values()),
        )
    finally:
        _close_connector(connector)


# ---------------------------------------------------------------------------
# Data lake / lakehouse sources (Trino, Databricks, Athena, DuckDB, ClickHouse,
# BigQuery, Spark). Metadata queries go through Wren's own connectors so that
# the connection semantics match execution exactly.
# ---------------------------------------------------------------------------


def _wren_connector(conn: ConnectionConfig):
    from wren.connector.factory import get_connector
    from wren.model.data_source import DataSource

    data_source = DataSource(conn.dialect)
    info = data_source.get_connection_info(conn.wren_connection_info())
    return get_connector(data_source, info)


def _close_connector(connector) -> None:
    try:
        connector.close()
    except Exception:
        pass


def _query_dicts(connector, sql: str) -> list[dict[str, Any]]:
    table = connector.query(sql)
    return table.to_pylist()


def _sql_str(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _quote_lake(dialect: str, value: str) -> str:
    if dialect in ("mysql", "databricks", "clickhouse", "spark", "bigquery"):
        return "`" + value.replace("`", "``") + "`"
    return '"' + value.replace('"', '""') + '"'


def _qualified_lake(dialect: str, catalog: str | None, schema: str | None, table: str) -> str:
    parts = []
    if catalog:
        parts.append(_quote_lake(dialect, catalog))
    if schema:
        parts.append(_quote_lake(dialect, schema))
    parts.append(_quote_lake(dialect, table))
    return ".".join(parts)


def _lake_md_type(dialect: str, raw_type: str) -> str:
    raw = (raw_type or "").strip()
    lower = raw.lower()
    while True:
        changed = False
        for wrapper in ("nullable", "lowcardinality"):
            prefix = wrapper + "("
            if lower.startswith(prefix) and lower.endswith(")"):
                raw = raw[len(prefix):-1].strip()
                lower = raw.lower()
                changed = True
        if not changed:
            break
    base = re.split(r"[(<\s]", lower, maxsplit=1)[0].strip()
    if base in {
        "tinyint", "smallint", "mediumint", "int", "integer", "int32",
        "int16", "int8", "uint8", "uint16", "uint32", "smallint unsigned",
        "tinyint unsigned", "int unsigned",
    }:
        return "INTEGER"
    if base in {"bigint", "int64", "uint64", "long", "bigint unsigned"}:
        return "BIGINT"
    if base in {"float", "float32", "real", "double", "float64", "float64"}:
        return "DOUBLE"
    if base in {"decimal", "numeric", "bignumeric", "number"}:
        return "DECIMAL"
    if base in {"bool", "boolean"}:
        return "BOOLEAN"
    if base in {"date", "date32"}:
        return "DATE"
    if base.startswith("timestamp") or base in {
        "datetime", "datetime64", "timestamp_ntz", "timestamp_ltz", "timestamp_tz",
    }:
        return "TIMESTAMP"
    if base in {"time"}:
        return "TIME"
    if base in {"json", "jsonb", "variant", "object"}:
        return "JSON"
    return "VARCHAR"


def _normalize_table_type(raw: str | None) -> str:
    value = (raw or "TABLE").upper()
    return "VIEW" if "VIEW" in value else "TABLE"


def _apply_pk_heuristic(table: TableInfo) -> None:
    if table.primary_key:
        return
    for column in table.columns:
        if column.name.lower() == "id":
            table.primary_key = [column.name]
            column.is_primary_key = True
            column.role = "key"
            return


def _finalize_columns(table: TableInfo) -> None:
    for column in table.columns:
        column.role = infer_role(column.name, column.mdl_type, column.is_primary_key or column.is_foreign_key)


def _sample_rows_lake(connector, dialect: str, table: TableInfo, limit: int = 3) -> list[dict[str, Any]]:
    sql = (
        f"SELECT * FROM {_qualified_lake(dialect, table.catalog, table.schema_name, table.name)} "
        f"LIMIT {limit}"
    )
    try:
        return _query_dicts(connector, sql)
    except Exception:
        return []


def _fill_samples_lake(
    connector,
    dialect: str,
    table: TableInfo,
    samples: list[dict[str, Any]],
) -> None:
    for col in table.columns:
        values: list[str] = []
        for row in samples:
            text = _stringify(row.get(col.name))
            if text and text not in values:
                values.append(text)
        low = col.name.lower()
        # Distinct-value probing is cheap on local DuckDB/ClickHouse. Cloud lake
        # queries can be expensive, so there we rely on the sample rows only.
        if (
            dialect in ("duckdb", "clickhouse")
            and col.mdl_type == "VARCHAR"
            and any(hint in low for hint in CATEGORICAL_HINTS)
        ):
            sql = (
                f"SELECT {_quote_lake(dialect, col.name)} AS v, COUNT(*) AS c "
                f"FROM {_qualified_lake(dialect, table.catalog, table.schema_name, table.name)} "
                f"WHERE {_quote_lake(dialect, col.name)} IS NOT NULL "
                f"GROUP BY 1 ORDER BY c DESC LIMIT 12"
            )
            try:
                for row in _query_dicts(connector, sql):
                    value = _stringify(row.get("v"))
                    if value and value not in values:
                        values.append(value)
            except Exception:
                pass
        col.sample_values = values[:12]


def _scan_lake(conn: ConnectionConfig) -> ScannedSchema:
    connector = _wren_connector(conn)
    try:
        if conn.dialect == "trino":
            tables, catalog, schema = _lake_trino(connector, conn)
        elif conn.dialect == "databricks":
            tables, catalog, schema = _lake_databricks(connector, conn)
        elif conn.dialect == "athena":
            tables, catalog, schema = _lake_athena(connector, conn)
        elif conn.dialect == "duckdb":
            tables, catalog, schema = _lake_duckdb(connector, conn)
        elif conn.dialect == "clickhouse":
            tables, catalog, schema = _lake_clickhouse(connector, conn)
        elif conn.dialect == "bigquery":
            tables, catalog, schema = _lake_bigquery(connector, conn)
        elif conn.dialect == "spark":
            tables, catalog, schema = _lake_spark(connector, conn)
        else:
            raise ValueError(f"Unsupported data source: {conn.dialect}")

        for table in tables:
            _finalize_columns(table)
            if table.table_type != "VIEW":
                _apply_pk_heuristic(table)
            samples = _sample_rows_lake(connector, conn.dialect, table)
            _fill_samples_lake(connector, conn.dialect, table, samples)

        return ScannedSchema(
            dialect=conn.dialect,
            database=conn.database,
            catalog=catalog,
            schema_name=schema,
            tables=tables,
        )
    finally:
        _close_connector(connector)


def _lake_trino(connector, conn: ConnectionConfig):
    catalog = conn.options.get("catalog") or conn.database or "hive"
    schema = conn.schema_name or "default"
    table_rows = _query_dicts(
        connector,
        f'SELECT table_name, table_type FROM "{catalog}".information_schema.tables '
        f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name",
    )
    comments: dict[str, str] = {}
    try:
        for row in _query_dicts(
            connector,
            "SELECT table_name, comment FROM system.metadata.table_comments "
            f"WHERE catalog_name = {_sql_str(catalog)} AND schema_name = {_sql_str(schema)}",
        ):
            comments[row["table_name"]] = row.get("comment") or ""
    except Exception:
        pass
    tables: dict[str, TableInfo] = {}
    for row in table_rows:
        name = row["table_name"]
        tables[name] = TableInfo(
            name=name,
            schema_name=schema,
            catalog=catalog,
            comment=comments.get(name) or None,
            table_type=_normalize_table_type(row.get("table_type")),
        )
    try:
        column_rows = _query_dicts(
            connector,
            f'SELECT table_name, column_name, data_type, is_nullable, ordinal_position, '
            f'column_default, comment FROM "{catalog}".information_schema.columns '
            f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name, ordinal_position",
        )
    except Exception:
        column_rows = _query_dicts(
            connector,
            f'SELECT table_name, column_name, data_type, is_nullable, ordinal_position, '
            f'column_default FROM "{catalog}".information_schema.columns '
            f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name, ordinal_position",
        )
    for row in column_rows:
        table = tables.get(row["table_name"])
        if not table:
            continue
        table.columns.append(
            ColumnInfo(
                name=row["column_name"],
                data_type=row.get("data_type") or "",
                mdl_type=_lake_md_type("trino", row.get("data_type") or ""),
                nullable=(str(row.get("is_nullable", "YES")).upper() == "YES"),
                default=str(row.get("column_default")) if row.get("column_default") is not None else None,
                comment=row.get("comment"),
                ordinal=int(row.get("ordinal_position") or 0),
            )
        )
    return list(tables.values()), catalog, schema


def _lake_databricks(connector, conn: ConnectionConfig):
    catalog = conn.options.get("catalog") or conn.database or "main"
    schema = conn.schema_name or "default"
    try:
        table_rows = _query_dicts(
            connector,
            "SELECT table_name, table_type, storage_location FROM system.information_schema.tables "
            f"WHERE table_catalog = {_sql_str(catalog)} AND table_schema = {_sql_str(schema)} "
            "ORDER BY table_name",
        )
    except Exception:
        table_rows = _query_dicts(
            connector,
            f"SELECT table_name, table_type, storage_location FROM `{catalog}`.information_schema.tables "
            f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name",
        )
    tables: dict[str, TableInfo] = {}
    for row in table_rows:
        name = row["table_name"]
        tables[name] = TableInfo(
            name=name,
            schema_name=schema,
            catalog=catalog,
            table_type=_normalize_table_type(row.get("table_type")),
            location=row.get("storage_location"),
        )
    try:
        column_rows = _query_dicts(
            connector,
            "SELECT table_name, column_name, data_type, is_nullable, ordinal_position, "
            "column_default, comment FROM system.information_schema.columns "
            f"WHERE table_catalog = {_sql_str(catalog)} AND table_schema = {_sql_str(schema)} "
            "ORDER BY table_name, ordinal_position",
        )
    except Exception:
        column_rows = _query_dicts(
            connector,
            f"SELECT table_name, column_name, data_type, is_nullable, ordinal_position, "
            f"column_default, comment FROM `{catalog}`.information_schema.columns "
            f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name, ordinal_position",
        )
    for row in column_rows:
        table = tables.get(row["table_name"])
        if not table:
            continue
        table.columns.append(
            ColumnInfo(
                name=row["column_name"],
                data_type=row.get("data_type") or "",
                mdl_type=_lake_md_type("databricks", row.get("data_type") or ""),
                nullable=(str(row.get("is_nullable", "YES")).upper() == "YES"),
                default=str(row.get("column_default")) if row.get("column_default") is not None else None,
                comment=row.get("comment"),
                ordinal=int(row.get("ordinal_position") or 0),
            )
        )
    return list(tables.values()), catalog, schema


def _lake_athena(connector, conn: ConnectionConfig):
    schema = conn.schema_name or "default"
    table_rows = _query_dicts(
        connector,
        "SELECT table_name, table_type FROM information_schema.tables "
        f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name",
    )
    tables: dict[str, TableInfo] = {}
    for row in table_rows:
        name = row["table_name"]
        tables[name] = TableInfo(
            name=name,
            schema_name=schema,
            catalog=None,
            table_type=_normalize_table_type(row.get("table_type")),
        )
    try:
        column_rows = _query_dicts(
            connector,
            "SELECT table_name, column_name, data_type, is_nullable, ordinal_position, comment "
            "FROM information_schema.columns "
            f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name, ordinal_position",
        )
    except Exception:
        column_rows = _query_dicts(
            connector,
            "SELECT table_name, column_name, data_type, is_nullable, ordinal_position "
            "FROM information_schema.columns "
            f"WHERE table_schema = {_sql_str(schema)} ORDER BY table_name, ordinal_position",
        )
    for row in column_rows:
        table = tables.get(row["table_name"])
        if not table:
            continue
        table.columns.append(
            ColumnInfo(
                name=row["column_name"],
                data_type=row.get("data_type") or "",
                mdl_type=_lake_md_type("athena", row.get("data_type") or ""),
                nullable=(str(row.get("is_nullable", "YES")).upper() == "YES"),
                comment=row.get("comment"),
                ordinal=int(row.get("ordinal_position") or 0),
            )
        )
    return list(tables.values()), None, schema


def _lake_clickhouse(connector, conn: ConnectionConfig):
    database = conn.database or "default"
    try:
        table_rows = _query_dicts(
            connector,
            f"SELECT name, total_rows, engine, comment FROM system.tables "
            f"WHERE database = {_sql_str(database)} ORDER BY name",
        )
    except Exception:
        table_rows = _query_dicts(
            connector,
            f"SELECT name, total_rows, engine FROM system.tables "
            f"WHERE database = {_sql_str(database)} ORDER BY name",
        )
    tables: dict[str, TableInfo] = {}
    for row in table_rows:
        name = row["name"]
        engine = (row.get("engine") or "").lower()
        tables[name] = TableInfo(
            name=name,
            schema_name=database,
            catalog=None,
            comment=row.get("comment") or None,
            row_count=int(row.get("total_rows") or 0),
            row_count_estimated=True,
            table_type="VIEW" if "view" in engine else "TABLE",
        )
    try:
        column_rows = _query_dicts(
            connector,
            "SELECT table, name, type, comment, position FROM system.columns "
            f"WHERE database = {_sql_str(database)} ORDER BY table, position",
        )
    except Exception:
        column_rows = _query_dicts(
            connector,
            "SELECT table, name, type, position FROM system.columns "
            f"WHERE database = {_sql_str(database)} ORDER BY table, position",
        )
    for row in column_rows:
        table = tables.get(row["table"])
        if not table:
            continue
        table.columns.append(
            ColumnInfo(
                name=row["name"],
                data_type=row.get("type") or "",
                mdl_type=_lake_md_type("clickhouse", row.get("type") or ""),
                nullable="nullable" in (row.get("type") or "").lower(),
                comment=row.get("comment"),
                ordinal=int(row.get("position") or 0),
            )
        )
    return list(tables.values()), None, database


def _lake_duckdb(connector, conn: ConnectionConfig):
    wanted_catalog = conn.options.get("catalog") or ""
    wanted_schema = conn.schema_name or "main"
    filters = [
        "schema_name NOT IN ('information_schema', 'pg_catalog')",
        "database_name NOT IN ('system', 'temp')",
    ]
    if wanted_catalog:
        filters.append(f"database_name = {_sql_str(wanted_catalog)}")
    if wanted_schema:
        filters.append(f"schema_name = {_sql_str(wanted_schema)}")
    where = " AND ".join(filters)
    table_rows = _query_dicts(
        connector,
        "SELECT database_name, schema_name, table_name, estimated_size "
        f"FROM duckdb_tables() WHERE {where} ORDER BY database_name, schema_name, table_name",
    )
    try:
        view_rows = _query_dicts(
            connector,
            "SELECT database_name, schema_name, view_name AS table_name, sql "
            f"FROM duckdb_views() WHERE internal = false AND {where} "
            "ORDER BY database_name, schema_name, table_name",
        )
    except Exception:
        view_rows = []
    tables: dict[tuple[str, str, str], TableInfo] = {}
    for row in table_rows:
        key = (row["database_name"], row["schema_name"], row["table_name"])
        tables[key] = TableInfo(
            name=row["table_name"],
            schema_name=row["schema_name"],
            catalog=row["database_name"],
            row_count=int(row.get("estimated_size") or 0),
            row_count_estimated=True,
            table_type="TABLE",
        )
    for row in view_rows:
        key = (row["database_name"], row["schema_name"], row["table_name"])
        sql_text = row.get("sql") or ""
        location = None
        match = re.search(r"(?:read_parquet|read_csv_auto|read_json_auto)\s*\(\s*'([^']+)'", sql_text, re.I)
        if match:
            location = match.group(1)
        tables[key] = TableInfo(
            name=row["table_name"],
            schema_name=row["schema_name"],
            catalog=row["database_name"],
            table_type="VIEW",
            location=location,
        )
    try:
        column_rows = _query_dicts(
            connector,
            "SELECT database_name, schema_name, table_name, column_name, data_type, "
            "is_nullable, column_index, comment FROM duckdb_columns() "
            f"WHERE {where} ORDER BY database_name, schema_name, table_name, column_index",
        )
    except Exception:
        column_rows = _query_dicts(
            connector,
            "SELECT database_name, schema_name, table_name, column_name, data_type, "
            "is_nullable, column_index FROM duckdb_columns() "
            f"WHERE {where} ORDER BY database_name, schema_name, table_name, column_index",
        )
    for row in column_rows:
        key = (row["database_name"], row["schema_name"], row["table_name"])
        table = tables.get(key)
        if not table:
            continue
        table.columns.append(
            ColumnInfo(
                name=row["column_name"],
                data_type=row.get("data_type") or "",
                mdl_type=_lake_md_type("duckdb", row.get("data_type") or ""),
                nullable=bool(row.get("is_nullable", True)),
                comment=row.get("comment"),
                ordinal=int(row.get("column_index") or 0),
            )
        )
    try:
        for row in _query_dicts(
            connector,
            "SELECT database_name, schema_name, table_name, constraint_column_names "
            "FROM duckdb_constraints() WHERE constraint_type = 'PRIMARY KEY'",
        ):
            key = (row["database_name"], row["schema_name"], row["table_name"])
            table = tables.get(key)
            if not table:
                continue
            columns = row.get("constraint_column_names") or []
            if isinstance(columns, str):
                columns = [columns]
            table.primary_key = list(columns)
            for column in table.columns:
                if column.name in table.primary_key:
                    column.is_primary_key = True
    except Exception:
        pass
    # Views have no estimated_size; count them locally so provenance/freshness
    # has a real row count. DuckDB is local, so this is cheap.
    for table in tables.values():
        if table.table_type != "VIEW" or table.row_count:
            continue
        try:
            rows = _query_dicts(
                connector,
                f"SELECT COUNT(*) AS n FROM "
                f"{_qualified_lake('duckdb', table.catalog, table.schema_name, table.name)}",
            )
            table.row_count = int(rows[0].get("n") or 0) if rows else 0
            table.row_count_estimated = False
        except Exception:
            pass
    catalog = wanted_catalog or (next(iter(tables.values())).catalog if tables else None)
    return list(tables.values()), catalog, wanted_schema


def _lake_bigquery(connector, conn: ConnectionConfig):
    project = conn.options.get("project_id") or conn.database
    dataset = conn.schema_name
    table_rows = _query_dicts(
        connector,
        f"SELECT table_name, table_type FROM `{project}.{dataset}.INFORMATION_SCHEMA.TABLES` "
        "ORDER BY table_name",
    )
    tables: dict[str, TableInfo] = {}
    for row in table_rows:
        name = row["table_name"]
        tables[name] = TableInfo(
            name=name,
            schema_name=dataset,
            catalog=project,
            table_type=_normalize_table_type(row.get("table_type")),
        )
    column_rows = _query_dicts(
        connector,
        "SELECT table_name, column_name, data_type, is_nullable, ordinal_position "
        f"FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS` "
        "ORDER BY table_name, ordinal_position",
    )
    for row in column_rows:
        table = tables.get(row["table_name"])
        if not table:
            continue
        table.columns.append(
            ColumnInfo(
                name=row["column_name"],
                data_type=row.get("data_type") or "",
                mdl_type=_lake_md_type("bigquery", row.get("data_type") or ""),
                nullable=(str(row.get("is_nullable", "YES")).upper() == "YES"),
                ordinal=int(row.get("ordinal_position") or 0),
            )
        )
    return list(tables.values()), project, dataset


def _lake_spark(connector, conn: ConnectionConfig):
    schema = conn.schema_name or "default"
    table_rows = _query_dicts(connector, f"SHOW TABLES IN {_quote_lake('spark', schema)}")
    tables: dict[str, TableInfo] = {}
    for row in table_rows:
        name = row.get("tableName") or row.get("table_name") or row.get("name")
        if not name:
            continue
        tables[name] = TableInfo(
            name=name,
            schema_name=schema,
            catalog=None,
            table_type="TABLE",
        )
    for table in tables.values():
        try:
            describe = _query_dicts(
                connector,
                f"DESCRIBE TABLE {_quote_lake('spark', schema)}.{_quote_lake('spark', table.name)}",
            )
        except Exception:
            describe = []
        for row in describe:
            name = row.get("col_name") or ""
            if not name or name.startswith("#"):
                continue
            table.columns.append(
                ColumnInfo(
                    name=name,
                    data_type=row.get("data_type") or "",
                    mdl_type=_lake_md_type("spark", row.get("data_type") or ""),
                    nullable=True,
                    comment=row.get("comment"),
                    ordinal=len(table.columns) + 1,
                )
            )
    return list(tables.values()), None, schema
