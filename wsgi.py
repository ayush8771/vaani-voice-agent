"""Production entry point: gunicorn wsgi:app"""

from app import _bootstrap_rag, create_app

_bootstrap_rag()
app = create_app()
