import os
import sys
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .engine import SpectraEngine
from .util import sha256_hex

app = FastAPI(title="Spectra OS Kernel", version="0.3.1")

engine = SpectraEngine()

_API_TOKEN = os.environ.get("SPECTRA_TOKEN")


@app.on_event("startup")
def startup_event():
    res = engine.boot()
    if res.get("attempted", 0) > 0:
        print(
            f"[Spectra] Boot complete: {res['success']} mounted, {res['failed']} failed.",
            file=sys.stderr,
        )


class MountRequest(BaseModel):
    path: str
    secret: Optional[str] = None
    verify: bool = True


class IndexRequest(BaseModel):
    mount_id: Optional[str] = None


class ChatRequest(BaseModel):
    question: str
    top_k: int = 7


def require_token(x_spectra_token: Optional[str] = Header(default=None)) -> None:
    if not _API_TOKEN:
        return
    if x_spectra_token != _API_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid Spectra Token")


def get_token_hash(x_spectra_token: Optional[str] = Header(default=None)) -> Optional[str]:
    if x_spectra_token:
        return sha256_hex(x_spectra_token)[:12]
    return None


@app.get("/")
def root() -> Dict[str, Any]:
    return {"system": "Spectra OS", "status": "online", "version": "0.3.1"}


@app.get("/health")
def health(_auth: None = Depends(require_token)) -> Dict[str, Any]:
    return engine.health()


@app.post("/mount")
def mount_shard(
    req: MountRequest,
    _auth: None = Depends(require_token),
    t_hash: Optional[str] = Depends(get_token_hash),
) -> Dict[str, Any]:
    try:
        out = engine.mount(req.path, req.secret, verify=req.verify, token_hash=t_hash)
        out["auth_enabled"] = bool(_API_TOKEN)
        return out
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/unmount/{mount_id}")
def unmount_shard(
    mount_id: str,
    _auth: None = Depends(require_token),
    t_hash: Optional[str] = Depends(get_token_hash),
) -> Dict[str, Any]:
    engine.unmount(mount_id, token_hash=t_hash)
    return {"status": "ok", "mount_id": mount_id}


@app.get("/catalog")
def get_catalog(_auth: None = Depends(require_token)) -> Dict[str, Any]:
    return engine.catalog_json()


@app.post("/query")
def query_sql(
    req: Dict[str, str],
    _auth: None = Depends(require_token),
    t_hash: Optional[str] = Depends(get_token_hash),
) -> Dict[str, Any]:
    sql = req.get("sql", "")
    try:
        return engine.query_json(sql, token_hash=t_hash)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/index")
def index_claims(
    req: IndexRequest,
    _auth: None = Depends(require_token),
    t_hash: Optional[str] = Depends(get_token_hash),
) -> Dict[str, Any]:
    try:
        return engine.index(mount_id=req.mount_id, token_hash=t_hash)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/chat")
def chat(
    req: ChatRequest,
    _auth: None = Depends(require_token),
    t_hash: Optional[str] = Depends(get_token_hash),
) -> Dict[str, Any]:
    try:
        if engine.index_size() == 0:
            return {"status": "error", "message": "Index empty. Mount a shard and call /index first."}
        out = engine.chat(req.question, top_k=req.top_k, token_hash=t_hash)
        out["status"] = "ok"
        return out
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
