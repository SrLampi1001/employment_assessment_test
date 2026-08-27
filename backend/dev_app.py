"""Dev entrypoint — wraps `create_app(Settings.from_env())` for uvicorn."""
from app.config import Settings
from app.main import create_app


app = create_app(Settings.from_env())
