"""WSGI entrypoint.

Waitress/Gunicorn import this module and serve the Flask `app`.
"""

from app import app

__all__ = ["app"]
