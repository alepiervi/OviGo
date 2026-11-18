# Un solo servizio: il server Python serve anche il sito.
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend/public frontend/public
WORKDIR /app/backend
ENV PORT=8000
CMD uvicorn server:app --host 0.0.0.0 --port ${PORT}
