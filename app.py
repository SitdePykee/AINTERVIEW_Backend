from flask import Flask
from blueprints.question import question_bp
from blueprints.interview import interview_bp
from blueprints.syllabus import syllabus_bp
import config

def create_app():
    app = Flask(__name__)
    app.register_blueprint(question_bp, url_prefix="/question")
    app.register_blueprint(interview_bp, url_prefix="/interview")
    app.register_blueprint(syllabus_bp, url_prefix="/syllabus")
    return app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
