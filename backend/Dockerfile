FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN addgroup --system paperguard && adduser --system --ingroup paperguard paperguard
RUN mkdir -p /app/data/uploads && chown -R paperguard:paperguard /app
USER paperguard

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
