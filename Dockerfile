FROM python:3.10-slim
WORKDIR /app
COPY up.v1.py requirements.txt ./
COPY languages/ ./languages/
RUN pip install --no-cache-dir -r requirements.txt
CMD ["python", "up.v1.py"]
