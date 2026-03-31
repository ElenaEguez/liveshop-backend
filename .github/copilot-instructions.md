# LiveShop Bolivia — Copilot Instructions

This document is a quick reference for any AI agent that will be working in the
LiveShop codebase.  It focuses on the project-specific architecture, naming
conventions and workflows that differ from generic Django projects.  Always
look here first before guessing how something should be done.

---

## 🏗  High‑level architecture

- **Backend**: single Django project (`config/`) with several apps: `users`,
  `vendors`, `products`, `livestreams`, `orders`, `payments` and
  `notifications`.  Each app follows the standard Django layout
  (`models.py`, `views.py`, `admin.py`, `tests.py`, `migrations/`).

- **API**: everything exposed via Django REST Framework (serializers and
  viewsets are created inside each app).  The Angular frontend (not yet
  present) will consume these endpoints.

- **Real‑time**: `django-channels` + `daphne` provide WebSocket support for
  live‑stream interactions.  A `channels_redis` layer backed by Redis is used
  for both channel layers and as the Celery broker.

- **Asynchronous tasks**: Celery (`celery==5.x`) runs alongside Redis.  Tasks
  will typically live in each app (e.g. `orders/tasks.py` once implemented).

- **Database**: PostgreSQL in production; `DATABASE_URL` / individual `DB_*`
  env vars are parsed with `django-environ`.

---

## ⚙  Configuration patterns

- All writable configuration lives in `config/settings.py`.  The file uses
  [`django-environ`](https://django-environ.readthedocs.io/) to pull values
  from a `.env` file located in the project root.  A template is provided as
  `.env.example`.

- Settings of interest that are *not* hardcoded:
  ```python
  SECRET_KEY          # env SECRET_KEY
  DEBUG               # boolean
  ALLOWED_HOSTS       # comma‑separated list
  DATABASE_URL / DB_* # postgres connection string
  REDIS_URL           # redis://… used by channels & celery
  CORS_ALLOWED_ORIGINS    # Angular usually runs on http://localhost:4200
  ```

- Installed apps include three groups in this order:
  1. Django built‑ins
  2. third‑party (`rest_framework`, `corsheaders`, `channels`)
  3. local apps (see list above)

- `MIDDLEWARE` must have `corsheaders.middleware.CorsMiddleware` near the
  top so that Angular can talk to the API.

- Channels configuration is defined in `CHANNEL_LAYERS` with the default
  backend `channels_redis.core.RedisChannelLayer`.

- `ASGI_APPLICATION` is set to `config.asgi.application`; `asgi.py` has a
  stubbed `ProtocolTypeRouter` waiting for `websocket` routes to be added
  (typically from `livestreams/routing.py`).

---

## 💻  Typical developer workflows

1. **Setup**
   ```bash
   python -m venv .venv
   .\.venv\Scripts\activate     # Windows
   pip install -r requirements.txt
   cp .env.example .env           # fill in secrets & DB credentials
   ```

2. **Database**
   ```bash
   python manage.py migrate
   python manage.py createsuperuser   # optional
   ```

3. **Run servers**
   - HTTP: `python manage.py runserver` (Channels' runserver works as ASGI)
   - WebSocket/production: `daphne config.asgi:application` (or via
     `uvicorn`/`hypercorn` later)
   - Celery worker: `celery -A config worker -l info` (ensure `REDIS_URL` is
     set)

4. **Tests**
   ```bash
   python manage.py test        # runs `tests.py` in each app
   ```
   Keep an eye on the simple, currently‑empty test files when you add logic.

5. **Front‑end integration**
   - The Angular dev server listens on `localhost:4200` by default; add its
     origin to `CORS_ALLOWED_ORIGINS` or export the same via `.env`.
   - All API endpoints should be namespaced (e.g. `/api/v1/users/`).  Add
     `include()` entries in `config/urls.py` when app urls are created.

6. **Environment variables**
   - Changes to `.env` require a server restart.  Use plain strings; the
     `django-environ` helpers (`env.bool`, `env.list`, `env.db_url`) will
     coerce types.
   - Do **not** commit `.env` to source control; only `.env.example` is tracked.

---

## 🧠 Notes for AI coding agents

- **Find the right app** by the domain of the change.  Avoid putting new
  models or logic in `config/` unless it's cross‑cutting.

- **Follow the stub‑first convention**: most files are empty skeletons.  Add
  import statements and class definitions as needed; existing imports are
  minimal.

- **Real‑time code** goes under `livestreams/`; e.g. `consumers.py` for
  WebSocket consumers, `routing.py` for URL patterns.  Use `channels` helpers
  such as `@database_sync_to_async` when touching the ORM inside async code.

- **Celery tasks** should be defined with `@shared_task` and imported in the
  app's `__init__.py` to ensure worker autodiscovery.  Use Redis as the broker
  (`BROKER_URL` from `REDIS_URL`) and `CELERY_RESULT_BACKEND` if needed.

- **Serializers and viewsets**: the DRF style is to keep serializers in
  `serializers.py` and viewsets in `views.py`.  Register routes using a
  `DefaultRouter` in each app's `urls.py` and then include those in
  `config/urls.py`.

- **Avoid hard‑coding** secrets, URLs, or ports.  If you need a new setting,
  expose it through `django-environ` and add a comment in `.env.example`.

- **Naming conventions**:
  - Models: `PascalCase` singular (e.g. `Product`, `VendorProfile`).
  - Serializers: `<ModelName>Serializer`.
  - API endpoints: `/api/v1/<app_name>/...` (versioning may be added later).

- **When in doubt**, run `python manage.py runserver` and check the URL
  pattern list.  Use `grep` or the search tools to locate similarly named
  functions or settings.

---

Please review this file and let me know if any section is unclear or missing
important project‑specific knowledge.  I'm happy to iterate! 
