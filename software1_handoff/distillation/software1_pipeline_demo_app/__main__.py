"""``python -m distillation.software1_pipeline_demo_app`` from repo root."""
from distillation.software1_pipeline_demo_app.app import create_app

if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8765, debug=False)
