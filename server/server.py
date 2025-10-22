#!/usr/bin/env python3
import asyncio
import json
import os
import base64
import io
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from urllib.parse import urlparse

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from jose import jwt, JWTError
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# -----------------------
# Configuración / Logging
# -----------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("remote_manager")

SECRET_KEY = os.environ.get("JWT_SECRET", "change_this_secret_in_production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./server.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

app = FastAPI(title="RemoteManager")

# static files
if not os.path.isdir("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# -----------------------
# Modelos DB
# -----------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password_hash = Column(String)
    role = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class ClientEntry(Base):
    __tablename__ = "clients"
    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(String, unique=True, index=True)
    name = Column(String, default="")
    hostname = Column(String, default="")
    last_seen = Column(DateTime, default=datetime.utcnow)
    connected = Column(Boolean, default=False)

Base.metadata.create_all(bind=engine)

# -----------------------
# Utilidades
# -----------------------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str):
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_user_from_token_sync(request: Request, db):
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing auth token")
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise HTTPException(status_code=401, detail="Invalid token payload")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def require_role(user, allowed_roles: List[str]):
    if user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions")

# -----------------------
# Globals (concurrencia-friendly)
# -----------------------
clients_ws: Dict[str, WebSocket] = {}       # client_id -> WebSocket
clients_ws_lock = asyncio.Lock()

latest_frames: Dict[str, bytes] = {}
frames_lock = asyncio.Lock()

last_seen_map: Dict[str, datetime] = {}     # keep last timestamp per client (do NOT clear aggressively)
last_seen_lock = asyncio.Lock()

exec_futures_global: Dict[str, Dict] = {}   # cmd_id -> {"future": Future, "created_at": datetime}
exec_futures_lock = asyncio.Lock()

_background_tasks = {
    "flush_last_seen": None,
    "cleanup_futures": None
}

# -----------------------
# Tunables (configurables vía env)
# -----------------------
FRAME_SIZE_LIMIT_BYTES = int(os.environ.get("FRAME_SIZE_LIMIT_BYTES", str(500 * 1024)))
LAST_SEEN_FLUSH_INTERVAL = float(os.environ.get("LAST_SEEN_FLUSH_INTERVAL", "5.0"))  # seconds between DB flushes
HEARTBEAT_INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "5.0"))              # recommended client heartbeat
LAST_SEEN_TIMEOUT_SECONDS = float(os.environ.get(
    "LAST_SEEN_TIMEOUT_SECONDS",
    str(max(15.0, HEARTBEAT_INTERVAL * 3))
))
FUTURE_CLEANUP_INTERVAL = float(os.environ.get("FUTURE_CLEANUP_INTERVAL", "60.0"))

logger.info("Config: FRAME_SIZE_LIMIT_BYTES=%d LAST_SEEN_FLUSH_INTERVAL=%.1f HEARTBEAT_INTERVAL=%.1f LAST_SEEN_TIMEOUT_SECONDS=%.1f",
            FRAME_SIZE_LIMIT_BYTES, LAST_SEEN_FLUSH_INTERVAL, HEARTBEAT_INTERVAL, LAST_SEEN_TIMEOUT_SECONDS)

# -----------------------
# Schemas
# -----------------------
class UpdateUserReq(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None

class TokenReq(BaseModel):
    username: str
    password: str

class CreateUserReq(BaseModel):
    username: str
    password: str
    role: str

class SendMessageReq(BaseModel):
    client_ids: List[str]
    message: str
    message_type: Optional[str] = "fixed"     # 'fixed' | 'temporary' | 'hidden'
    timeout_seconds: Optional[int] = None     # aplicable si message_type == 'temporary'

class ExecReq(BaseModel):
    client_ids: List[str]
    command: Optional[str] = None
    timeout_seconds: Optional[int] = 10
    open_url: Optional[str] = None   # si se envía, abre enlace en cliente


# -----------------------
# Default users
# -----------------------
# Asegúrate de que tu pwd_context esté configurado así:
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)

def hash_password(password: str):
    # Si ya es un hash bcrypt, no lo re-hasheamos
    if password.startswith("$2a$") or password.startswith("$2b$") or password.startswith("$2y$"):
        return password

    # Truncamiento seguro a 72 bytes (límite de bcrypt)
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raw = raw[:72]
        password = raw.decode("utf-8", errors="ignore")

    return pwd_context.hash(password)


def ensure_default_users():
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            admin = User(
                username="admin",
                password_hash=hash_password("admin"),
                role="systems"
            )
            director = User(
                username="director",
                password_hash=hash_password("director_password"),
                role="director"
            )
            db.add(admin)
            db.add(director)
            db.commit()
            logger.info("Usuarios creados: admin/admin, director/director_password")
    finally:
        db.close()


ensure_default_users()


# -----------------------
# Auth & User CRUD
# -----------------------
@app.post("/login")
def login(req: TokenReq, db=Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.username, "role": user.role})
    return {"access_token": token, "token_type": "bearer", "role": user.role}

@app.post("/users", status_code=201)
def create_user(req: CreateUserReq, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=400, detail="User exists")
    u = User(username=req.username, password_hash=hash_password(req.password), role=req.role)
    db.add(u); db.commit()
    return {"ok": True}

@app.get("/users")
def list_users(request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    users = db.query(User).all()
    return [{"username": u.username, "role": u.role} for u in users]

@app.get("/users/{username}")
def get_user(username: str, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    u = db.query(User).filter(User.username == username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": u.username, "role": u.role, "created_at": u.created_at.isoformat()}

@app.put("/users/{username}")
def update_user(username: str, req: UpdateUserReq, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    u = db.query(User).filter(User.username == username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if req.password:
        u.password_hash = hash_password(req.password)
    if req.role:
        u.role = req.role
    db.commit()
    return {"ok": True}

@app.delete("/users/{username}")
def delete_user(username: str, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    u = db.query(User).filter(User.username == username).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if u.username == "admin":
        raise HTTPException(status_code=400, detail="Cannot delete admin user")
    db.delete(u)
    db.commit()
    return {"ok": True}

# -----------------------
# Clients metadata
# -----------------------
@app.get("/clients")
def get_clients(request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems", "director"])
    entries = db.query(ClientEntry).all()
    return [{
        "client_id": c.client_id,
        "name": c.name,
        "hostname": c.hostname,
        "last_seen": c.last_seen.isoformat() if c.last_seen else None,
        "connected": bool(c.connected)
    } for c in entries]

@app.post("/clients/{client_id}/name")
async def set_client_name(client_id: str, payload: dict, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    c = db.query(ClientEntry).filter(ClientEntry.client_id == client_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Client not found")
    c.name = payload.get("name", c.name)
    db.commit()
    # notify client (best-effort)
    async with clients_ws_lock:
        ws = clients_ws.get(client_id)
    if ws:
        try:
            await ws.send_text(json.dumps({"type": "set_name", "name": c.name}))
        except Exception as e:
            logger.warning("Failed to notify client %s about name change: %s", client_id, e)
    return {"ok": True}

# -----------------------
# Messaging (broadcast/send)
# -----------------------
@app.post("/send_message")
async def send_message(req: SendMessageReq, request: Request, db=Depends(get_db)):
    """
    Envia un mensaje a los clientes listados en req.client_ids.

    Payload WebSocket enviado a cada cliente:
      {
        "type": "message",
        "message": "...",
        "message_type": "fixed" | "temporary" | "hidden",
        "timeout_seconds": 5   # si aplica (temporal)
      }

    Compatibilidad: los clientes que no sepan de message_type simplemente
    deberán ignorar los campos extra y procesar "message" como antes.
    """
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems", "director"])
    results = {}

    # Normalizar message_type
    mt = (req.message_type or "fixed").lower()
    if mt not in ("fixed", "temporary", "hidden"):
        mt = "fixed"

    # Si es temporal, validar timeout (valor por defecto 5s si no se provee)
    timeout_val = None
    if mt == "temporary":
        try:
            timeout_val = int(req.timeout_seconds) if req.timeout_seconds is not None else 5
            if timeout_val <= 0:
                timeout_val = 5
        except Exception:
            timeout_val = 5

    tasks = []
    target_ids = []

    # Construir tareas de envío
    async with clients_ws_lock:
        for cid in req.client_ids:
            ws = clients_ws.get(cid)
            if ws:
                async def _send(ws_ref, cid_ref):
                    try:
                        payload = {
                            "type": "message",
                            "message": req.message,
                            "message_type": mt
                        }
                        if mt == "temporary":
                            payload["timeout_seconds"] = timeout_val
                        # para message_type == 'hidden' dejamos content en payload para que
                        # el cliente lo pueda almacenar y mostrar cuando se pulse "ver"
                        try:
                            await ws_ref.send_text(json.dumps(payload))
                            return ("sent", None)
                        except Exception as e:
                            return ("send_failed", str(e))
                    except Exception as ex:
                        return ("send_failed", str(ex))

                tasks.append(_send(ws, cid))
                target_ids.append(cid)
            else:
                results[cid] = "offline"

    # Ejecutar envíos concurrentes si hay tareas
    if tasks:
        send_results = await asyncio.gather(*tasks, return_exceptions=True)
        for cid, r in zip(target_ids, send_results):
            if isinstance(r, Exception):
                results[cid] = f"send_failed: {r}"
            else:
                status, err = r
                results[cid] = "sent" if status == "sent" else f"{status}: {err}"

    # Responder con texto (compatible con frontend que espera texto)
    if results and all(v == "sent" for v in results.values()):
        return PlainTextResponse("Mensaje enviado correctamente ✅")
    else:
        # Si no hay entradas en results (por ejemplo lista vacía), devolver info neutra
        if not results:
            return PlainTextResponse("No se enviaron mensajes (lista de clientes vacía).")
        return PlainTextResponse("Resultados: " + json.dumps(results, ensure_ascii=False))

# -----------------------
# Remote exec (futures)
# -----------------------
async def _create_exec_future(cmd_id: str) -> asyncio.Future:
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    async with exec_futures_lock:
        exec_futures_global[cmd_id] = {"future": fut, "created_at": datetime.utcnow()}
    return fut

async def _pop_exec_future(cmd_id: str):
    async with exec_futures_lock:
        entry = exec_futures_global.pop(cmd_id, None)
    return entry

@app.post("/exec")
async def exec_command(req: ExecReq, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    responses = {}

    # 1) Si viene open_url: manejamos solo esa rama y retornamos
    if req.open_url:
        url = (req.open_url or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="open_url vacío")

        parsed = urlparse(url)
        if not parsed.scheme:
            url = "https://" + url
            parsed = urlparse(url)
        if not parsed.netloc:
            raise HTTPException(status_code=400, detail="open_url inválido")

        async with clients_ws_lock:
            tasks = []
            target_ids = []
            for cid in req.client_ids:
                ws = clients_ws.get(cid)
                if ws:
                    async def _send_open(ws_ref, cid_ref):
                        try:
                            await ws_ref.send_text(json.dumps({"type": "open_url", "url": url}))
                            return ("sent", None)
                        except Exception as e:
                            return ("send_failed", str(e))
                    tasks.append(_send_open(ws, cid))
                    target_ids.append(cid)
                else:
                    responses[cid] = "offline"

        if tasks:
            send_results = await asyncio.gather(*tasks, return_exceptions=True)
            for cid, r in zip(target_ids, send_results):
                if isinstance(r, Exception):
                    responses[cid] = f"send_failed: {r}"
                else:
                    status, err = r
                    responses[cid] = "sent" if status == "sent" else f"{status}: {err}"

        return {"responses": responses}

    # 2) Si NO es open_url → ejecutamos el comando como antes (tu código original)
    async def send_and_wait(cid, cmd, timeout):
        async with clients_ws_lock:
            ws = clients_ws.get(cid)
        if not ws:
            return {"error": "offline"}
        cmd_id = f"{cid}-{uuid.uuid4().hex}"
        try:
            fut = await _create_exec_future(cmd_id)
            try:
                await ws.send_text(json.dumps({"type": "exec", "command": cmd, "cmd_id": cmd_id}))
            except Exception as e:
                await _pop_exec_future(cmd_id)
                return {"error": f"send_failed: {e}"}
            try:
                res = await asyncio.wait_for(fut, timeout=timeout)
                return {"result": res}
            except asyncio.TimeoutError:
                await _pop_exec_future(cmd_id)
                return {"error": "timeout"}
            except Exception as e:
                await _pop_exec_future(cmd_id)
                return {"error": f"future_error: {e}"}
        except Exception as e:
            return {"error": f"internal_error: {e}"}

    tasks = [send_and_wait(cid, req.command, req.timeout_seconds) for cid in req.client_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for cid, r in zip(req.client_ids, results):
        if isinstance(r, Exception):
            responses[cid] = {"error": str(r)}
        else:
            responses[cid] = r
    return {"responses": responses}

# -----------------------
# Screen endpoints
# -----------------------
@app.get("/clients/{client_id}/screen")
async def get_client_screen(client_id: str, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems", "director"])
    async with frames_lock:
        frame = latest_frames.get(client_id)
    if not frame:
        raise HTTPException(status_code=404, detail="No screen frame available")
    return StreamingResponse(io.BytesIO(frame), media_type="image/jpeg")

@app.post("/clients/{client_id}/screen/start")
async def start_screen(client_id: str, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems", "director"])
    async with clients_ws_lock:
        ws = clients_ws.get(client_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Client not connected")
    try:
        await ws.send_text(json.dumps({"type": "start_screen_stream"}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"send_failed: {e}")
    return {"ok": True}

@app.post("/clients/{client_id}/screen/stop")
async def stop_screen(client_id: str, request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems", "director"])
    async with clients_ws_lock:
        ws = clients_ws.get(client_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Client not connected")
    try:
        await ws.send_text(json.dumps({"type": "stop_screen_stream"}))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"send_failed: {e}")
    return {"ok": True}

# -----------------------
# Background tasks
# -----------------------
async def flush_last_seen_task():
    """
    Periodically flush in-memory last_seen_map to DB.
    - We DO NOT clear last_seen_map: keep latest timestamp per client.
    - Use LAST_SEEN_TIMEOUT_SECONDS to consider stale clients.
    - Do not mark disconnected if client still has an in-memory websocket (avoids flapping).
    """
    logger.info("Starting flush_last_seen_task")
    try:
        while True:
            await asyncio.sleep(LAST_SEEN_FLUSH_INTERVAL)
            now = datetime.utcnow()
            cutoff = now - timedelta(seconds=LAST_SEEN_TIMEOUT_SECONDS)

            # snapshot (do not clear; keep the latest known timestamps in memory)
            async with last_seen_lock:
                snapshot = dict(last_seen_map)

            db = SessionLocal()
            try:
                # determine which clients are currently connected (have an active websocket)
                async with clients_ws_lock:
                    active_ids = set(clients_ws.keys())

                for cid, ts in snapshot.items():
                    entry = db.query(ClientEntry).filter(ClientEntry.client_id == cid).first()
                    if entry:
                        if not entry.last_seen or ts > entry.last_seen:
                            entry.last_seen = ts
                        # mark connected only if client still has an active websocket
                        entry.connected = cid in active_ids
                    else:
                        db.add(ClientEntry(client_id=cid, last_seen=ts, connected=(cid in active_ids)))
                db.commit()


                # determine stale DB rows (last_seen < cutoff) but only mark disconnected
                # if they do NOT have an in-memory websocket (prevents race/flapping)
                async with clients_ws_lock:
                    in_memory_ids = set(clients_ws.keys())

                stale_rows = db.query(ClientEntry).filter(ClientEntry.last_seen < cutoff, ClientEntry.connected == True).all()
                to_mark = [r for r in stale_rows if r.client_id not in in_memory_ids]

                if to_mark:
                    for r in to_mark:
                        r.connected = False
                    db.commit()
                    logger.info("Marked %d clients disconnected due to stale last_seen and no WS", len(to_mark))

            except Exception as e:
                logger.exception("Error flushing last_seen to DB: %s", e)
            finally:
                db.close()
    except asyncio.CancelledError:
        logger.info("flush_last_seen_task cancelled")
        return

async def cleanup_stale_futures_task():
    logger.info("Starting cleanup_stale_futures_task")
    try:
        while True:
            await asyncio.sleep(FUTURE_CLEANUP_INTERVAL)
            cutoff = datetime.utcnow() - timedelta(seconds=FUTURE_CLEANUP_INTERVAL)
            to_cancel = []
            async with exec_futures_lock:
                for cid, entry in list(exec_futures_global.items()):
                    created = entry.get("created_at")
                    if created and created < cutoff:
                        to_cancel.append(cid)
            for cid in to_cancel:
                entry = None
                async with exec_futures_lock:
                    entry = exec_futures_global.pop(cid, None)
                if entry:
                    fut = entry.get("future")
                    if fut and not fut.done():
                        try:
                            fut.set_exception(RuntimeError("stale_future_cancelled"))
                        except Exception:
                            pass
                        logger.warning("Cancelled stale future %s", cid)
    except asyncio.CancelledError:
        logger.info("cleanup_stale_futures_task cancelled")
        return

# -----------------------
# WebSocket endpoint (clients)
# -----------------------
@app.websocket("/ws/client")
async def ws_client_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_id = None
    try:
        raw = await websocket.receive_text()
        try:
            msg = json.loads(raw)
        except Exception:
            await websocket.close()
            return

        if msg.get("type") != "register":
            await websocket.close()
            return
        client_id = msg.get("client_id")
        hostname = msg.get("hostname", "")
        name = msg.get("name", "")
        if not client_id:
            await websocket.close()
            return

        # register/update DB entry
        db = SessionLocal()
        try:
            entry = db.query(ClientEntry).filter(ClientEntry.client_id == client_id).first()
            if not entry:
                entry = ClientEntry(client_id=client_id, name=name, hostname=hostname, connected=True, last_seen=datetime.utcnow())
                db.add(entry)
            else:
                entry.hostname = hostname
                entry.name = name
                entry.connected = True
                entry.last_seen = datetime.utcnow()
            db.commit()
        except Exception as e:
            logger.exception("DB error during register: %s", e)
        finally:
            db.close()

        # add to in-memory map; if previous exists, close it (best-effort)
        async with clients_ws_lock:
            prev = clients_ws.get(client_id)
            if prev and prev is not websocket:
                try:
                    await prev.close()
                except Exception:
                    pass
            clients_ws[client_id] = websocket

        # update last_seen_map
        async with last_seen_lock:
            last_seen_map[client_id] = datetime.utcnow()

        logger.info("[WS] Client connected: %s (%s)", client_id, hostname)

        # receive loop
        while True:
            data = await websocket.receive_text()
            # update last seen whenever we get any message
            async with last_seen_lock:
                last_seen_map[client_id] = datetime.utcnow()
            try:
                msg = json.loads(data)
            except Exception:
                continue
            mtype = msg.get("type")
            if mtype == "heartbeat":
                # explicit heartbeat: already updated last_seen above
                pass
            elif mtype == "cmd_result":
                cmd_id = msg.get("cmd_id")
                async with exec_futures_lock:
                    entry = exec_futures_global.get(cmd_id)
                if entry:
                    fut = entry.get("future")
                    if fut and not fut.done():
                        fut.set_result(msg)
                        async with exec_futures_lock:
                            exec_futures_global.pop(cmd_id, None)
            elif mtype == "screen_frame":
                cid = msg.get("client_id")
                frame_b64 = msg.get("frame")
                if cid and frame_b64:
                    try:
                        frame_bytes = base64.b64decode(frame_b64)
                        if len(frame_bytes) > FRAME_SIZE_LIMIT_BYTES:
                            logger.warning("Dropping oversized frame from %s (%d bytes)", cid, len(frame_bytes))
                        else:
                            async with frames_lock:
                                latest_frames[cid] = frame_bytes
                    except Exception as e:
                        logger.exception("Error decoding frame from %s: %s", cid, e)
            else:
                logger.debug("Unknown WS message from %s: %s", client_id, mtype)

    except WebSocketDisconnect:
        logger.info("[WS] Client disconnected (socket): %s", client_id)
    except Exception as e:
        logger.exception("WS error for %s: %s", client_id, e)
    finally:
        # cleanup in-memory maps
        async with clients_ws_lock:
            if client_id in clients_ws:
                clients_ws.pop(client_id, None)
        # DB: mark disconnected and update last_seen
        db = SessionLocal()
        try:
            entry = db.query(ClientEntry).filter(ClientEntry.client_id == client_id).first()
            if entry:
                entry.connected = False
                entry.last_seen = datetime.utcnow()
                db.commit()
        except Exception as e:
            logger.exception("DB error on disconnect: %s", e)
        finally:
            db.close()
        logger.info("[WS] Client disconnected (cleanup): %s", client_id)

# -----------------------
# Health / status
# -----------------------
@app.get("/status")
async def status():
    num_clients = await _count_connected_clients()
    return {"connected_clients": num_clients, "frame_limit_bytes": FRAME_SIZE_LIMIT_BYTES}

async def _count_connected_clients():
    async with clients_ws_lock:
        return len(clients_ws)

# -----------------------
# Reconcile endpoint (async) - repair DB with in-memory state
# -----------------------
@app.post("/reconcile")
async def reconcile(request: Request, db=Depends(get_db)):
    current = get_user_from_token_sync(request, db)
    require_role(current, ["systems"])
    try:
        async with clients_ws_lock:
            client_ids = list(clients_ws.keys())
        # reset DB connected flags, then mark in-memory ones as connected
        db.query(ClientEntry).update({ClientEntry.connected: False})
        db.commit()
        now = datetime.utcnow()
        for cid in client_ids:
            entry = db.query(ClientEntry).filter(ClientEntry.client_id == cid).first()
            if entry:
                entry.connected = True
                entry.last_seen = now
            else:   
                db.add(ClientEntry(client_id=cid, connected=True, last_seen=now))
        db.commit()
        return {"ok": True, "reconciled_clients": len(client_ids)}
    except Exception as e:
        logger.exception("Error during reconcile: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    
# -----------------------
# Startup / Shutdown
# -----------------------
@app.on_event("startup")
async def startup_event():
    logger.info("Starting RemoteManager server")
    # reset DB connected flags at startup to avoid stale 'True' after crash/restart
    db = SessionLocal()
    try:
        try:
            db.query(ClientEntry).update({ClientEntry.connected: False})
            db.commit()
            logger.info("All client entries marked disconnected on startup")
        except Exception as e:
            logger.exception("Error resetting client connected flags on startup: %s", e)
    finally:
        db.close()

    loop = asyncio.get_running_loop()
    _background_tasks["flush_last_seen"] = loop.create_task(flush_last_seen_task())
    _background_tasks["cleanup_futures"] = loop.create_task(cleanup_stale_futures_task())

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down RemoteManager server")
    # cancel background tasks
    for name, task in _background_tasks.items():
        if task:
            task.cancel()
            try:
                await task
            except Exception:
                pass
    # close websockets (best-effort)
    async with clients_ws_lock:
        for cid, ws in list(clients_ws.items()):
            try:
                await ws.close()
            except Exception:
                pass

# -----------------------
# Root
# -----------------------
@app.get("/", response_class=HTMLResponse)
def root():
    if os.path.exists("server/static/index.html"):
        return FileResponse("server/static/index.html")
    return HTMLResponse("<h1>RemoteManager</h1><p>API running.</p>")

# -----------------------
# Run (if executed directly)
# -----------------------
if __name__ == "__main__":
    import uvicorn
    logger.info("Server running on http://0.0.0.0:9000")
    uvicorn.run("server:app", host="0.0.0.0", port=9000, log_level="info", workers=1)
