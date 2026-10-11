FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt
COPY backend/*.py /app/backend/
COPY frontend/index.html frontend/app.js frontend/styles.css /app/frontend/
COPY db/validacion/ /app/db/validacion/
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
