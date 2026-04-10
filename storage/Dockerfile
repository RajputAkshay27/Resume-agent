FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY storage/pyproject.toml ./
RUN pip install --no-cache-dir fastapi uvicorn boto3 python-multipart python-dotenv pydantic

# Copy source code
COPY storage/main.py ./

EXPOSE 8001

CMD ["uvicorn", "main.py:app", "--host", "0.0.0.0", "--port", "8001"]
