# RepliCore

A shared drawing board that keeps working when servers crash.
Three replicas agree on every stroke using the **RAFT consensus algorithm**, so there is no central database and no single point of failure.

The codebase is intentionally small and fully commented. See **[GUIDE.md](GUIDE.md)** for a step-by-step walkthrough of how it works.

```
 Browser tabs ──WebSocket──►  Gateway (Node.js, port 3000)
                                 │  finds the leader, forwards strokes
                                 ▼
          ┌──────────── Leader replica ────────────┐
          │ AppendEntries (strokes + heartbeats)    │
          ▼                                         ▼
   Follower replica                          Follower replica
   (Python, port 5002)                       (Python, port 5003)
```

## Project structure

| File | What it does | Lines |
|---|---|---|
| `replica/raft.py` | The RAFT algorithm: elections, log replication, commits | ~390 |
| `replica/server.py` | Puts a RAFT node on the network (FastAPI) | ~110 |
| `gateway/index.js` | Finds the leader, forwards strokes, live updates to browsers | ~175 |
| `frontend/index.html` | The page | ~40 |
| `frontend/app.js` | Drawing on the canvas + cluster dashboard | ~175 |
| `docker-compose.yml` | Starts 3 replicas + gateway with one command | ~40 |

## Run it

**With Docker** (Docker Desktop must be running):

```powershell
docker compose up --build
```

**Without Docker** (Python 3.10+ and Node.js 18+):

```powershell
pip install -r replica\requirements.txt
cd gateway; npm install; cd ..
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
```

Then open **http://localhost:3000** in two or three tabs and draw.

**Test it:** `python tests/test_cluster.py` runs 10 automatic checks (election, replication, failover, catch-up, majority).
