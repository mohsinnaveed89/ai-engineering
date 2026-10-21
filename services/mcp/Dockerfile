FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY packages/kyc_common /packages/kyc_common
RUN pip install /packages/kyc_common
COPY services/mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/mcp/server.py .
EXPOSE 8100
CMD ["python", "server.py"]
