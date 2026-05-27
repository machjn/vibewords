FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir .

COPY static/ ./static/
COPY config.yaml .

ENV PORT=8080

CMD uvicorn vibewords.main:app --host 0.0.0.0 --port ${PORT}
