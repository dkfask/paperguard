FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
ARG PAPERGUARD_REQUIRE_BUILD_IDENTITY=0
RUN python -m app.build_identity --require "$PAPERGUARD_REQUIRE_BUILD_IDENTITY"

RUN addgroup --system paperguard && adduser --system --ingroup paperguard paperguard
RUN mkdir -p /app/data/uploads && chown -R paperguard:paperguard /app
USER paperguard

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
