"""Thin helpers over the D1 binding.

D1 is reached through the Workers binding (env.DB), which returns JavaScript
objects; everything here hands back plain Python dicts so the rest of the code
never touches a JsProxy.
"""


def _to_py(value):
    return value.to_py() if hasattr(value, "to_py") else value


def _statement(db, sql, params):
    stmt = db.prepare(sql)
    return stmt.bind(*params) if params else stmt


async def query(db, sql, *params):
    """Run a SELECT and return every row as a dict."""
    result = await _statement(db, sql, params).all()
    return [dict(_to_py(row)) for row in _to_py(result.results)]


async def query_one(db, sql, *params):
    """Run a SELECT and return the first row, or None."""
    row = await _statement(db, sql, params).first()
    if row is None:
        return None
    return dict(_to_py(row))


async def execute(db, sql, *params):
    """Run a write. D1 auto-commits each statement."""
    await _statement(db, sql, params).run()
