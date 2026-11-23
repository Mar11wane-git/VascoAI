# =====================================================================
# Stage 1: Build the React Frontend (Vite)
# =====================================================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/react-app
COPY react-app/package*.json ./
RUN npm install
COPY react-app/ ./
RUN npm run build

# =====================================================================
# Stage 2: Python 3.12 FastAPI Production Server
# =====================================================================
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=7860

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY --from=frontend-builder /app/frontend ./frontend
COPY run.py ./

EXPOSE 7860

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
