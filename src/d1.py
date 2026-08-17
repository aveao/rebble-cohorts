"""Thin helpers over the D1 binding.

The workers SDK wraps the binding, so results arrive as dict-like views over the
JavaScript objects rather than raw JsProxies. Copying each row into a real dict
here keeps the rest of the code on plain Python values.
"""


def _statement(db, sql, params):
    stmt = db.prepare(sql)
    return stmt.bind(*params) if params else stmt


async def query(db, sql, *params):
    """Run a SELECT and return every row as a dict."""
    result = await _statement(db, sql, params).all()
    return [dict(row) for row in result.results]


async def query_one(db, sql, *params):
    """Run a SELECT and return the first row, or None."""
    row = await _statement(db, sql, params).first()
    return None if row is None else dict(row)


async def execute(db, sql, *params):
    """Run a write. D1 auto-commits each statement."""
    await _statement(db, sql, params).run()
