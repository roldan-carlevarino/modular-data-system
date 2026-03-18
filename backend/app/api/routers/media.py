from fastapi import APIRouter, Query, HTTPException, UploadFile, File, Form
from b2sdk.v2 import InMemoryAccountInfo, B2Api
import os
import mimetypes
import time

router = APIRouter()

_b2_api = None
_b2_bucket = None


def _get_b2():
    """Lazy-init B2 connection (cached after first call)."""
    global _b2_api, _b2_bucket
    if _b2_api is None or _b2_bucket is None:
        key_id      = os.getenv("B2_KEY_ID")
        app_key     = os.getenv("B2_APPLICATION_KEY")
        bucket_name = os.getenv("B2_BUCKET_NAME")

        if not key_id or not app_key or not bucket_name:
            raise HTTPException(
                status_code=500,
                detail="B2 credentials not configured (B2_KEY_ID / B2_APPLICATION_KEY / B2_BUCKET_NAME)"
            )

        try:
            info = InMemoryAccountInfo()
            api = B2Api(info)
            api.authorize_account("production", key_id, app_key)
            # get_bucket_by_name() fails with restricted keys (lists all buckets internally).
            # Use bucket ID directly if available, otherwise fall back to name.
            bucket_id = os.getenv("B2_BUCKET_ID")
            if bucket_id:
                bucket = api.get_bucket_by_id(bucket_id)
            else:
                bucket = api.get_bucket_by_name(bucket_name)
            # Only cache once fully initialised
            _b2_api = api
            _b2_bucket = bucket
        except Exception as e:
            # Don't cache a broken state
            _b2_api = None
            _b2_bucket = None
            raise HTTPException(status_code=500, detail=f"B2 init error: {e}")

    return _b2_api, _b2_bucket


@router.get("/media/signed-url")
def get_signed_url(file: str = Query(..., description="File path inside the bucket, e.g. photos/img.jpg")):
    """
    Returns a short-lived signed URL for a private B2 file.
    TTL: 3600 seconds (1 hour).
    """
    try:
        api, bucket = _get_b2()
        token = bucket.get_download_authorization(
            file_name_prefix=file,
            valid_duration_in_seconds=3600,
        )
        base_url = api.get_download_url_for_file_name(bucket.name, file)
        return {"url": f"{base_url}?Authorization={token}", "expires_in": 3600}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/media/upload")
async def upload_file(
    file: UploadFile = File(...),
    concept_id: int = Form(...),
    block_id: int = Form(...),
):
    """
    Uploads a file to B2 under intel/{concept_id}/{block_id}/{timestamp}_{original_name}.
    Returns the b2:// path ready to use in markdown.
    """
    try:
        api, bucket = _get_b2()

        content = await file.read()
        safe_name = f"{int(time.time())}_{file.filename.replace(' ', '_')}"
        b2_path = f"intel/{concept_id}/{block_id}/{safe_name}"

        content_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"

        bucket.upload_bytes(
            data_bytes=content,
            file_name=b2_path,
            content_type=content_type,
        )

        return {"path": b2_path, "b2_ref": f"b2://{b2_path}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/media/list")
def list_files(prefix: str = Query(default="", description="Folder prefix to list, e.g. 'photos/'")):
    """
    Lists files in the bucket under the given prefix.
    Useful for building a file picker in the UI.
    """
    try:
        api, bucket = _get_b2()
        files = []
        for file_version, _ in bucket.ls(folder_to_list=prefix or "", recursive=True):
            files.append({
                "name": file_version.file_name,
                "size": file_version.size,
                "content_type": file_version.content_type,
            })
        return {"files": files}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
