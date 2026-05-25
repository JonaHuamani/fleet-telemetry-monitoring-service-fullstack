from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws import broadcaster

router = APIRouter()


@router.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    await broadcaster.connect(ws)
    try:
        while True:
            # We do not expect client messages; keep the connection alive.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.disconnect(ws)
