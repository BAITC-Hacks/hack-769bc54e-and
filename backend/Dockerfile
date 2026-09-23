FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# 1 worker + потоки: фоновые запуски агента живут в потоках этого процесса
CMD ["sh", "-c", "python manage.py migrate -v0 && gunicorn config.wsgi -b 0.0.0.0:8000 --workers 1 --threads 8 --timeout 120"]
