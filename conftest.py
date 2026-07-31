"""pytest root configuration.

The presence of this file at the repository root makes pytest insert the root
directory into ``sys.path``, so tests can ``import text`` (and later app
modules) without packaging.
"""
