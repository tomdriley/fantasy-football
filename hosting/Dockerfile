FROM python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727

ARG RELEASE=development
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FFOPT_HOSTING_ENVIRONMENT=stage \
    FFOPT_HOSTING_RELEASE=${RELEASE}
LABEL org.opencontainers.image.title="Fantasy football hosting probe" \
      org.opencontainers.image.revision=${RELEASE}

WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.txt -c requirements.lock \
    && groupadd --gid 10001 hosting \
    && useradd --uid 10001 --gid 10001 --no-create-home hosting

COPY --chown=10001:10001 hosting/__init__.py hosting/app.py hosting/database.py hosting/sample.py ./hosting/
COPY --chown=10001:10001 hosting/static/index.html ./hosting/static/index.html

USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "hosting.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-proxy-headers", "--limit-concurrency", "20", "--timeout-keep-alive", "5", "--timeout-graceful-shutdown", "30"]
