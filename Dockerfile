# --- build the web UI
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- API + static UI in one image
FROM python:3.12-slim
ENV TZ=Asia/Tehran PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HESABDAR_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/frontend/dist
RUN useradd -m hesabdar && mkdir -p /app/backend/data && chown -R hesabdar /app/backend/data
USER hesabdar
EXPOSE 8000
CMD ["python", "manage.py", "run", "--host", "0.0.0.0", "--port", "8000"]
