FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY resolution resolution
COPY data data
COPY scripts scripts
RUN useradd --create-home resolution && mkdir -p /app/work && chown -R resolution:resolution /app
USER resolution
ENV PYTHONUNBUFFERED=1 REFUND_WORK_DIR=/app/work PYTHONPATH=/app
EXPOSE 8103
CMD ["python", "scripts/serve.py"]
