from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from typing import Optional

# Importamos utilidades y modelos desde server (server debe definirlos antes de importar users)
from server.server import get_db, User, hash_password, require_role, get_user_from_token_sync

router = APIRouter()

class UpdateUserReq(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None

@router.get("/users/{username}")
def get_user(username: str, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["admin"])
    u = db.query(User).filter(User.username == username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": u.username, "role": u.role, "created_at": u.created_at.isoformat()}

@router.put("/users/{username}")
def update_user(username: str, req: UpdateUserReq, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["admin"])
    u = db.query(User).filter(User.username == username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if req.password:
        u.password_hash = hash_password(req.password)
    if req.role:
        u.role = req.role
    db.commit()
    return {"ok": True}

@router.delete("/users/{username}")
def delete_user(username: str, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["admin"])
    u = db.query(User).filter(User.username == username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(u)
    db.commit()
    return {"ok": True}