# ELSEWHERE single-service API image (mock-mode default: no QLOO_API_KEY needed).
# Serves the committed frontend/dist SPA via backend/app/main.py StaticFiles;
# live keys only ever come from untracked .env (see .env.example, .dockerignore).
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
