"""Tool test fixtures.

This file intentionally avoids global ``sys.modules`` mocking.

Historically these tests were forced to run on Python 3.9, so the suite
replaced many in-repo ``tools.*`` modules with ``MagicMock`` objects during
collection.  The project now targets Python 3.11+, and those global mocks
break real imports for the entire tools test package.
"""
