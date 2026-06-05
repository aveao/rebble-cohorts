def post_fork(server, worker):
    # Warm up the connection pool so the first request to DB isn't super slow.
    # This is done here so that it's per-worker, and this delays the readiness
    # state until after this connection is done. May require a higher delay for
    # readiness probe.
    from cohorts import app
    from cohorts.models import db

    with app.app_context():
        # close here doesn't close the pool but releases the connection.
        db.engine.connect().close()
