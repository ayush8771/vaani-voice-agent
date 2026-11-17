"""Production entry point: gunicorn wsgi:app"""

from app import _bootstrap_rag, _warm_up_llm, create_app

_bootstrap_rag()
_warm_up_llm()
app = create_app()
