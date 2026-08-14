from ipaddress import ip_address

from fastapi import HTTPException, Request

from app.runtime.config import load_rag_console_runtime_settings


def require_rag_console(request: Request):
    settings = load_rag_console_runtime_settings()
    if not settings.diagnostic_ui_enabled or not _is_loopback(request):
        raise HTTPException(status_code=404, detail="not found")
    return settings


def require_live_inspector(request: Request):
    settings = require_rag_console(request)
    if not settings.live_inspector_enabled:
        raise HTTPException(status_code=404, detail="not found")
    return settings


def require_eval_artifacts(request: Request):
    settings = require_rag_console(request)
    if not settings.eval_artifact_access_enabled:
        raise HTTPException(status_code=404, detail="not found")
    return settings


def require_corpus_write(request: Request):
    settings = require_rag_console(request)
    if not settings.corpus_write_enabled:
        raise HTTPException(status_code=404, detail="not found")
    return settings


def _is_loopback(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    try:
        return ip_address(client.host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False
