FROM python:3.12-slim

WORKDIR /app

COPY app/services/office_worker/dependency.json /tmp/office-dependency.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer openjdk-21-jre-headless \
    && python -c 'import hashlib,json,pathlib,urllib.request; d=json.load(open("/tmp/office-dependency.json")); p=pathlib.Path("/app/office/libreoffice.jar"); p.parent.mkdir(parents=True); urllib.request.urlretrieve(d["url"],p); actual=hashlib.sha256(p.read_bytes()).hexdigest(); assert actual==d["sha256"], f"LibreOffice UNO JAR SHA-256 mismatch: {actual}"' \
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
