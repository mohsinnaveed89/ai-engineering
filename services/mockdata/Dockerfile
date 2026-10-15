FROM python:3.12-slim
WORKDIR /app
COPY services/mockdata/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/mockdata/main.py .
ENV DATA_DIR=/data
EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
