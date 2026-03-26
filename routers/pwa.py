import os
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from deps import templates

router = APIRouter()


@router.get("/sw.js")
async def service_worker():
    return FileResponse("static/sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


@router.get("/offline", response_class=HTMLResponse)
async def offline(request: Request):
    return templates.TemplateResponse(request, "offline.html")


@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@router.get("/.well-known/assetlinks.json")
async def assetlinks():
    fingerprint = os.getenv("ASSET_LINK_FINGERPRINT", "")
    if not fingerprint:
        return JSONResponse([])
    return JSONResponse([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": os.getenv("ANDROID_PACKAGE_NAME", "com.contentmarket.app"),
            "sha256_cert_fingerprints": [fingerprint],
        }
    }])
