FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PYTHONPATH=/srv
WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app/ .
RUN useradd -r -u 10001 app
USER 10001
EXPOSE 8000
CMD ["python", "main.py"]
