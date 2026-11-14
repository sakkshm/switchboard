FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV TIKTOKEN_CACHE_DIR=/app/.token-cache
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY app ./app
COPY catalog ./catalog
COPY scripts ./scripts
COPY static ./static
COPY evals ./evals
RUN mkdir -p /app/.data

EXPOSE 8000
CMD ["sh", "-c", "python scripts/seed.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
