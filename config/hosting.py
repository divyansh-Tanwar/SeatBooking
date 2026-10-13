"""Parse provider connection URLs without logging credentials."""

from urllib.parse import parse_qs, unquote, urlsplit


def database_from_url(value):
    url = urlsplit(value)
    if url.scheme not in ("postgres", "postgresql") or not url.hostname or not url.path.strip("/"):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL with a database name")
    query = parse_qs(url.query)
    options = {"connect_timeout": 15}
    for key in ("sslmode", "channel_binding"):
        if key in query:
            options[key] = query[key][-1]
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(url.path.lstrip("/")),
        "USER": unquote(url.username or ""),
        "PASSWORD": unquote(url.password or ""),
        "HOST": url.hostname,
        "PORT": str(url.port or 5432),
        "CONN_MAX_AGE": 0,
        "DISABLE_SERVER_SIDE_CURSORS": True,
        "OPTIONS": options,
    }
