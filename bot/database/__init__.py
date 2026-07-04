"""Database package.

Importing this package ensures all ORM model modules are registered with
``Base.metadata``.  Alembic's ``env.py`` imports this package (not individual
model files) so that a single ``from bot.database import models_all`` statement
is enough to make every table visible to ``autogenerate``.
"""

# These side-effect imports register every mapped class with Base.metadata.
# The order matters: models.py must come first because models_knowledge_ext
# adds a FK back to knowledge_entries defined there.
from bot.database import (  # noqa: F401 – side-effect imports intentional
    models,
    models_ai,
    models_knowledge_ext,
    models_moderation_intel,
)