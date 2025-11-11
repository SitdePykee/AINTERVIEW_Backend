from flask import Flask
from flask_cors import CORS
from blueprints.question import question_bp
from blueprints.interview import interview_bp
from blueprints.syllabus import syllabus_bp
from blueprints.authentication import auth_bp
from blueprints.systemCurriculum import curriculum_bp
from blueprints.revision import revision_bp


def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"*": {"origins": "*"}})

    @app.route('/')
    def index():
        return "Backend is running"

    app.register_blueprint(curriculum_bp, url_prefix='/system_curriculum')
    app.register_blueprint(question_bp, url_prefix="/question")
    app.register_blueprint(interview_bp, url_prefix="/interview")
    app.register_blueprint(syllabus_bp, url_prefix="/syllabus")
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(revision_bp, url_prefix="/revision")
    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
