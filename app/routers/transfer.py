"""
File transfer router.
Accepts multipart uploads, spawns a background thread running the TCP protocol,
and streams progress/log events to the client via Server-Sent Events (SSE).
"""
import asyncio
import json
import os
import tempfile
import threading
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import Machine
from ..protocol import send_file

router = APIRouter(prefix="/api/transfers", tags=["transfers"])

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

active_transfers: dict[str, "ActiveTransfer"] = {}

# One asyncio.Lock per machine IP.  A new transfer to a machine that already
# has an active transfer is rejected with 409 instead of queuing – this avoids
# interleaved chunk sends (which would corrupt the transfer) without risking a
# deadlock (we never hold more than one lock at a time and never await an
# already-held lock).
_machine_locks: dict[str, asyncio.Lock] = {}


class ActiveTransfer:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.cancel_event = threading.Event()

    def emit(self, event_type: str, data: Any) -> None:
        """Thread-safe: enqueue an SSE event from a background thread."""
        payload = {"event": event_type, "data": data}
        self.loop.call_soon_threadsafe(self.queue.put_nowait, payload)

    def progress_cb(self, sent_chunks: int, total_chunks: int) -> None:
        self.emit(
            "progress",
            {"sent": sent_chunks, "total": total_chunks},
        )

    def log_cb(self, message: str) -> None:
        self.emit("log", {"message": message})


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/", status_code=202)
async def start_transfer(
    machine_id: int = Form(...),
    file: UploadFile = Form(...),
    short_name: str = Form(""),
    db: Session = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """
    Upload an embroidery file and start a transfer to the given machine.
    Returns a transfer_id for subscribing to the SSE event stream.
    """
    # Validate machine
    machine = db.get(Machine, machine_id)
    if not machine:
        raise HTTPException(status_code=404, detail="Machine not found")

    # Enforce one active transfer per machine IP to prevent chunk interleaving.
    machine_ip = machine.ip
    if machine_ip not in _machine_locks:
        _machine_locks[machine_ip] = asyncio.Lock()
    lock = _machine_locks[machine_ip]
    if lock.locked():
        raise HTTPException(
            status_code=409,
            detail=f"Machine '{machine.name}' is already busy with another transfer.",
        )
    await lock.acquire()

    # Validate file extension
    filename = file.filename or "unknown.dst"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".dst", ".dsb"):
        raise HTTPException(
            status_code=400,
            detail="Only .dst and .dsb files are supported",
        )

    # Sanitize short name for the DOS 8.3 filename field:
    #   1. Keep only printable ASCII (0x20–0x7E) – strips umlauts, emojis, …
    #   2. Additionally strip characters that are illegal in FAT filesystems:
    #      / \ : * ? " < > |
    #      The dot (.) is also excluded: it is not part of the 8-byte name
    #      field in the wire format, but the machine reintroduces it when
    #      storing the file, so a dot in the name would produce a double-dot
    #      or confuse the embedded FS parser.
    _FAT_ILLEGAL = set('/\\:*?"<>|.')

    def _sanitize_name(s: str) -> str:
        return "".join(
            c for c in s if "\x20" <= c <= "\x7e" and c not in _FAT_ILLEGAL
        )[:8]

    if short_name:
        short_name = _sanitize_name(short_name)
        if not short_name:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The provided machine name contains no valid characters. "
                    "Only printable ASCII (space through ~) is allowed."
                ),
            )
    else:
        base = os.path.splitext(filename)[0]
        short_name = _sanitize_name(base) or "NONAME"

    # Save upload to a temp file
    suffix = ext
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        contents = await file.read()
        tmp.write(contents)
        tmp.flush()
        tmp.close()
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise

    transfer_id = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    transfer = ActiveTransfer(loop)
    active_transfers[transfer_id] = transfer

    def _run_transfer() -> None:
        try:
            send_file(
                filepath=tmp.name,
                machine_ip=machine_ip,
                short_name=short_name,
                progress_cb=transfer.progress_cb,
                log_cb=transfer.log_cb,
                cancel_event=transfer.cancel_event,
            )
            transfer.emit("done", {"message": "Transfer complete"})
        except Exception as exc:
            transfer.emit("error", {"message": str(exc)})
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            def _cleanup():
                active_transfers.pop(transfer_id, None)
                # Release the per-machine lock so new transfers can start.
                if lock.locked():
                    lock.release()

            loop.call_soon_threadsafe(_cleanup)

    thread = threading.Thread(target=_run_transfer, daemon=True)
    thread.start()

    return {"transfer_id": transfer_id}


@router.get("/{transfer_id}/events")
async def transfer_events(transfer_id: str):
    """SSE stream for transfer progress, log, done and error events."""
    transfer = active_transfers.get(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    async def event_generator() -> AsyncGenerator[dict, None]:
        while True:
            try:
                event = await asyncio.wait_for(transfer.queue.get(), timeout=30)
            except asyncio.TimeoutError:
                # Send a keep-alive comment
                yield {"event": "ping", "data": ""}
                continue

            yield {
                "event": event["event"],
                "data": json.dumps(event["data"]),
            }

            if event["event"] in ("done", "error"):
                break

    return EventSourceResponse(event_generator())


@router.post("/{transfer_id}/cancel")
async def cancel_transfer(
    transfer_id: str,
    _user: dict = Depends(get_current_user),
):
    """Signal the background thread to abort the current transfer."""
    transfer = active_transfers.get(transfer_id)
    if not transfer:
        raise HTTPException(status_code=404, detail="Transfer not found")

    transfer.cancel_event.set()
    return {"status": "cancelling"}
