# fastapi_app/asgi.py
"""
ASGI entry point for the FastAPI application.

This can be run with uvicorn:
    uvicorn fastapi_app.asgi:app --host 0.0.0.0 --port 8001

Or programmatically:
    import uvicorn
    from fastapi_app.asgi import app
    uvicorn.run(app, host="0.0.0.0", port=8001)
"""
from .main import app

__all__ = ['app']
