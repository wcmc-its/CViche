"""Service layer: business logic and data access functions.

Route handlers import from this package. Services import from models, database,
and errors -- never from api/. Dependency arrow: api/ -> services/ -> models/.
"""
