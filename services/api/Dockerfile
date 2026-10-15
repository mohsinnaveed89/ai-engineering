FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY packages/kyc_common /packages/kyc_common
RUN pip install /packages/kyc_common
COPY services/api/requirements.txt .
RUN pip install -r requirements.txt
COPY services/api/app ./app
EXPOSE 8000
# --reload picks up your edits to services/api/app without rebuilding (the folder is mounted in docker-compose.yml)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/srv/app"]
