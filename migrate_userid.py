import os
try:
    from flask import Flask
except ImportError as exc:
    raise ImportError("Flask is required to run this application. Install it with 'pip install flask'.") from exc

app = Flask(__name__)

app.secret_key = os.getenv("APP_SECRET_KEY")