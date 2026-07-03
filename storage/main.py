import os
import boto3
import logging
import base64
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import Response, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from contextlib import asynccontextmanager

load_dotenv()

# Logger setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("storage-service")

# S3 Configuration
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://localhost:3900")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "resume-agent-bucket")
S3_REGION = os.getenv("S3_REGION", "garage")
S3_SECURE = os.getenv("S3_SECURE", "false").lower() == "true"

# Service Auth
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "default_secret_key_change_me")

# CORS Configuration
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
LATEX_BACKEND_URL = os.getenv("LATEX_BACKEND_URL", "http://localhost:8002")

# --- Lifespan Events ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure bucket exists
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "404":
            logger.info(f"Bucket {S3_BUCKET} does not exist. Creating it.")
            s3.create_bucket(Bucket=S3_BUCKET)
        else:
            logger.error(f"Failed to check/create bucket: {e}")
    except Exception as e:
        logger.error(f"Could not connect to S3 on startup: {e}")
    
    yield
    # Shutdown logic (if any) can go here

app = FastAPI(title="Storage Service", version="1.0.0", lifespan=lifespan)

# Allow requests only from Frontend and Backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, BACKEND_URL, LATEX_BACKEND_URL],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

from botocore.config import Config

# S3 Client singleton with path-style addressing for Garage compatibility
s3_config = Config(s3={'addressing_style': 'path'})
s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT if S3_ENDPOINT.startswith("http") else f"http://{S3_ENDPOINT}",
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name=S3_REGION,
    use_ssl=S3_SECURE,
    config=s3_config,
)

# --- Dependency for service-level auth ---
async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != INTERNAL_API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden: Invalid API Key")
    return x_api_key

# --- Schemas ---
class UploadB64Request(BaseModel):
    key: str
    content_b64: str
    content_type: Optional[str] = "application/octet-stream"

class CopyRequest(BaseModel):
    source_key: str
    target_key: str

# --- Endpoints ---


@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/upload", dependencies=[Depends(verify_api_key)])
async def upload_file(file: UploadFile = File(...), key: Optional[str] = Form(None)):
    """Upload a file using multipart/form-data."""
    target_key = key or file.filename
    try:
        s3.upload_fileobj(
            file.file,
            S3_BUCKET,
            target_key,
            ExtraArgs={"ContentType": file.content_type}
        )
        return {"key": target_key, "success": True}
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload-base64", dependencies=[Depends(verify_api_key)])
async def upload_base64(req: UploadB64Request):
    """Upload a file using base64 encoded content."""
    try:
        content = base64.b64decode(req.content_b64)
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=req.key,
            Body=content,
            ContentType=req.content_type
        )
        return {"key": req.key, "success": True}
    except Exception as e:
        logger.error(f"Base64 Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

@app.get("/download/{key:path}", dependencies=[Depends(verify_api_key)])
async def download_file(key: str):
    """Download a file directly."""
    try:
        response = s3.get_object(Bucket=S3_BUCKET, Key=key)
        return StreamingResponse(
            response["Body"],
            media_type=response.get("ContentType", "application/octet-stream")
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            raise HTTPException(status_code=404, detail="File not found")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/delete/{key:path}", dependencies=[Depends(verify_api_key)])
async def delete_file(key: str):
    """Delete a file."""
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=key)
        return {"success": True}
    except Exception as e:
        logger.error(f"Delete error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/copy", dependencies=[Depends(verify_api_key)])
async def copy_file(req: CopyRequest):
    """Copy a file within the same bucket."""
    try:
        s3.copy_object(
            Bucket=S3_BUCKET,
            CopySource={'Bucket': S3_BUCKET, 'Key': req.source_key},
            Key=req.target_key
        )
        return {"success": True, "target_key": req.target_key}
    except Exception as e:
        logger.error(f"Copy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/list/{prefix:path}", dependencies=[Depends(verify_api_key)])
async def list_files(prefix: str = ""):
    """List files in a prefix."""
    try:
        response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
        contents = response.get("Contents", [])
        return {
            "files": [
                {"key": obj["Key"], "size": obj["Size"], "last_modified": obj["LastModified"].isoformat()}
                for obj in contents
            ]
        }
    except Exception as e:
        logger.error(f"List error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
