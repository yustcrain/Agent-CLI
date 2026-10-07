FROM python:3.11-slim
WORKDIR /opt/picli
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY picli ./picli
ENV PYTHONPATH=/opt/picli PYTHONDONTWRITEBYTECODE=1
WORKDIR /workspace
