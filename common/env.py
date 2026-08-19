"""
Single source of truth for environment-file resolution.

python-decouple's default ``AutoConfig`` only ever reads ``.env``. That is a
problem here for two reasons:

1. A developer's ``.env.local`` should win over the shared ``.env`` without
   anyone having to edit or clobber the latter.
2. Several modules (``ai/``, ``mcp/``, ``whatsapp_integration/``) call
   ``decouple.config`` directly at import time rather than reading
   ``django.conf.settings``. If only ``settings.py`` resolved the env file,
   those modules would silently fall back to their defaults -- which presents
   as "credentials not configured" errors that are very hard to trace, because
   the values are plainly sitting in ``.env.local``.

Import ``config`` from here instead of from ``decouple`` so every module agrees
on which file it is reading.

Precedence: ``DJANGO_ENV_FILE`` > ``.env.local`` > ``.env``.
``os.environ`` still takes priority over the file, because decouple checks it
first -- so a one-off ``VAR=x python manage.py ...`` override keeps working.
"""
import os
from pathlib import Path

from decouple import AutoConfig, Config, Csv, RepositoryEnv  # noqa: F401  (Csv re-exported)

BASE_DIR = Path(__file__).resolve().parent.parent


def resolve_env_file(base_dir=BASE_DIR):
    """Return the env file this process should read, or None if there isn't one."""
    explicit = os.environ.get('DJANGO_ENV_FILE')
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    for name in ('.env.local', '.env'):
        candidate = Path(base_dir) / name
        if candidate.is_file():
            return candidate
    return None


ENV_FILE = resolve_env_file()

if ENV_FILE is not None:
    config = Config(RepositoryEnv(str(ENV_FILE)))
else:
    # No env file at all (CI, containers with pure env vars). AutoConfig still
    # reads os.environ, so this degrades to "environment variables only"
    # rather than blowing up at import time.
    config = AutoConfig(search_path=str(BASE_DIR))
