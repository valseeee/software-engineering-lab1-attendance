from pathlib import Path

from flask import Flask

from config import Config
from models import db, init_db
from routes import main_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["FACE_TEMPLATE_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    app.register_blueprint(main_bp)

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=False)
