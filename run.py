"""Точка входа для локального запуска:

    py run.py                             # FastAPI на 127.0.0.1:8000
    py run.py pipeline 5                  # Ручной запуск пайплайна (5 лидов)
    py run.py analyze https://example.com # Анализ одного сайта
    py run.py analyze "Название компании" # Поиск на HH + анализ
    py run.py analyze hh:1234567          # Анализ по HH employer_id
"""
import sys

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "pipeline":
        from app.database import init_db
        from pipeline.runner import run_pipeline_once
        init_db()
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        result = run_pipeline_once(limit=limit)
        print("Result:", result)
    elif cmd == "analyze":
        if len(sys.argv) < 3:
            print("Usage: py run.py analyze <url | company-name | hh:id>")
            sys.exit(1)
        from pipeline.analyze_one import main
        main(sys.argv[2])
    else:
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
