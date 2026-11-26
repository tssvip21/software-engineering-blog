FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.lock requirements-deploy.txt ./
RUN pip install -r requirements-deploy.txt \
    && useradd --uid 10001 --create-home petnote
COPY manage.py ./
COPY config/ config/
COPY care/ care/
COPY templates/ templates/
COPY static/ static/
COPY deploy/entrypoint.sh deploy/entrypoint-demo.sh deploy/start-local.sh deploy/serve.py deploy/healthcheck.py deploy/source_files.py deploy/
RUN mkdir -p /app/data /app/staticfiles && chown -R petnote:petnote /app
USER petnote
EXPOSE 8000
ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
CMD ["python", "/app/deploy/serve.py"]
