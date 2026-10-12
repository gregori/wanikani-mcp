FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd -r -g 10001 wk && useradd -r -u 10001 -g wk wk

COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

USER wk
EXPOSE 8000
CMD ["wanikani-mcp"]
