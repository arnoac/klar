import sqlite3


def _py(v):
    if v == "SqlNull":
        return None
    return v[1]


def _klar(v):
    if v is None:
        return "SqlNull"
    if isinstance(v, bool):
        return ("SqlInt", int(v))
    if isinstance(v, int):
        return ("SqlInt", v)
    if isinstance(v, float):
        return ("SqlFloat", v)
    if isinstance(v, bytes):
        return ("SqlText", v.decode("utf-8", "replace"))
    return ("SqlText", str(v))


def open_db(path):
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    return conn


def execute(conn, sql, params):
    cur = conn.execute(sql, [_py(p) for p in params])
    return cur.rowcount if cur.rowcount != -1 else 0


def last_insert_id(conn):
    return conn.execute("select last_insert_rowid()").fetchone()[0]


def query(conn, sql, params):
    cur = conn.execute(sql, [_py(p) for p in params])
    cols = [d[0] for d in cur.description or []]
    return [{c: _klar(v) for c, v in zip(cols, row)} for row in cur.fetchall()]


def close(conn):
    conn.close()
