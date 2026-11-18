"""The software factory run as a small service: an API, a worker and, later, a web page.

The web process (``app.api``) reads runs and adds jobs. The worker (``app.worker``) is the
only process that runs the factory, so it is the only one that needs a model key. Both
share one SQLite file. ``app.factory`` is the one file that knows which factory it is.
"""
