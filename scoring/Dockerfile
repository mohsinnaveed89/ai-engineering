FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data REPORTS_DIR=/app/scoring/reports
COPY packages/kyc_common /packages/kyc_common
RUN pip install --no-cache-dir /packages/kyc_common
COPY scoring/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scoring/score.py ./scoring/score.py
ENTRYPOINT ["python", "scoring/score.py"]
