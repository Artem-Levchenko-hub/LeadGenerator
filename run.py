"""Точка входа для локального запуска:

    python run.py                  # FastAPI на 127.0.0.1:8000
    python run.py pipeline 5       # Ручной запуск пайплайна (5 лидов)
"""
import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pipeline":
        from app.database import init_db
        from pipeline.runner import run_pipeline_once
        init_db()
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        result = run_pipeline_once(limit=limit)
        print("Result:", result)
    else:
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
