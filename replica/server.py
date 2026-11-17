"""
server.py  -  puts the RAFT node on the network.

Each replica is a small web server. Other replicas and the gateway talk to it
with plain HTTP + JSON. This file only translates HTTP into calls on RaftNode.

Settings come from environment variables:
  NODE_ID   name of this node, e.g. node1
  PEERS     comma-separated URLs of the other nodes

Run it:   uvicorn server:app --port 5001
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from raft import RaftNode

NODE_ID = os.getenv("NODE_ID", "node1")
PEERS = [p.strip() for p in os.getenv("PEERS", "").split(",") if p.strip()]

app = FastAPI(title=f"RepliCore replica {NODE_ID}")
node = RaftNode(NODE_ID, PEERS)


# While "crashed", the node ignores everything except /recover and /status,
# exactly like a machine that is switched off (status is kept so the
# dashboard can show it as crashed).
@app.middleware("http")
async def pretend_to_be_dead(request: Request, call_next):
    if node.crashed and request.url.path not in ("/recover", "/status"):
        return JSONResponse({"error": "node is crashed"}, status_code=503)
    return await call_next(request)


# ---- Messages between replicas (the RAFT protocol) ------------------------

class VoteRequest(BaseModel):
    term: int
    candidate_id: str
    last_log_index: int
    last_log_term: int


class AppendEntriesRequest(BaseModel):
    term: int
    leader_id: str
    prev_log_index: int
    prev_log_term: int
    entries: list[dict]
    leader_commit: int


@app.post("/request-vote")
def request_vote(req: VoteRequest):
    return node.handle_request_vote(req.term, req.candidate_id, req.last_log_index, req.last_log_term)


@app.post("/append-entries")
def append_entries(req: AppendEntriesRequest):
    return node.handle_append_entries(
        req.term, req.leader_id, req.prev_log_index, req.prev_log_term, req.entries, req.leader_commit
    )


# ---- Used by the gateway ---------------------------------------------------

class SubmitRequest(BaseModel):
    command: dict          # {"type": "stroke", "stroke": {...}}  or  {"type": "clear"}


@app.post("/submit")
def submit(req: SubmitRequest):
    result = node.submit(req.command)
    if result is None:
        # Not the leader: tell the caller who is (if we know).
        return JSONResponse({"success": False, "error": "not leader", "leader": node.leader_id}, status_code=409)
    if result is False:
        return JSONResponse({"success": False, "error": "could not commit (no majority?)"}, status_code=503)
    return {"success": True}


@app.get("/status")
def status():
    return node.status()


@app.get("/board")
def board():
    return {"strokes": node.board()}


@app.get("/log")
def full_log():
    s = node.status()
    return {"commit_index": s["commit_index"], "entries": node.log}


# ---- Chaos buttons ----------------------------------------------------------

@app.post("/crash")
def crash():
    node.crash()
    return {"success": True}


@app.post("/recover")
def recover():
    node.recover()
    return {"success": True}
