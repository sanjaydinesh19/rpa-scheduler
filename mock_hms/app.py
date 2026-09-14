"""Entry point.

    Local:  python -m mock_hms.app          (or: flask --app mock_hms.app run)
    Render: gunicorn "mock_hms.app:app" --bind 0.0.0.0:$PORT
"""
import os

from . import create_app

app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "true").lower() == "true",
    )
