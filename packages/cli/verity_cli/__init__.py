"""The ``verity`` command line interface.

Contains no business logic. Every command delegates to the core packages, so
the CLI, the API, the web application and the GitHub Action can never disagree
about what a verdict means.

The entry point is :func:`verity_cli.main.main`. It is deliberately not
re-exported here: binding the name ``main`` in this package would shadow the
``verity_cli.main`` module for anything that imports it by path.
"""

__all__: list[str] = []
