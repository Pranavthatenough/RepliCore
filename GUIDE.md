# RepliCore – Developer Guide

This guide walks through the system in the order it was built. Each step covers **what to read**, **what to run**, and **what you should see**.
All commands are for **PowerShell** on Windows, run from the project folder.

---

## Step 0 – What are we building, and why?

Imagine a drawing board that many people use at once. The simple way: one server stores all strokes.
Problem: if that server dies, everything stops and maybe data is lost.

So we run **3 copies (replicas)** of the server. Now a new problem: how do the 3 copies stay **identical**
when strokes arrive, servers crash, and messages get delayed? That is what a **consensus algorithm** solves.
We use **RAFT**, the most common one (it powers etcd, Consul, CockroachDB, and more).

The three pieces:

| Piece | Language | Job |
|---|---|---|
| **Replica** ×3 | Python (FastAPI) | Runs RAFT. Stores the log of strokes. |
| **Gateway** | Node.js (Express + WebSocket) | The front door. Finds the leader and forwards strokes to it. |
| **Frontend** | HTML + JavaScript | The canvas you draw on and a live cluster dashboard. |

---

## Step 1 – RAFT in 5 ideas

Everything in `raft.py` implements one of these ideas.

1. **Roles.** Each node is a **follower**, a **candidate**, or the **leader**. Only the leader accepts new strokes.
2. **Heartbeats.** The leader sends a message to everyone every 150 ms: "I'm alive."
3. **Elections.** If a follower hears nothing for a random 0.6–1.2 s, it assumes the leader died.
   It becomes a candidate, increases the **term** (election round number) and asks for votes.
   Whoever gets a **majority** (2 of 3) becomes the new leader. The random wait stops everyone starting at once.
4. **The log.** Every stroke is an entry in an ordered list, the log. The leader adds it to its log
   and copies it to followers (the AppendEntries message, which doubles as the heartbeat).
5. **Commit.** An entry is **committed** (safe forever) once a majority has stored it.
   Only committed entries appear on the board. With 3 nodes, 1 can die and nothing is lost;
   if 2 die there is no majority, so the system refuses writes instead of risking wrong data.

> The leader has the newest data. A node can only win an election if its log is at least as up to date
> as the voter's (see "Rule 3" in `handle_request_vote`). This is why a committed stroke can never be lost.

---

## Step 2 – One replica on its own

**Read:** `replica/server.py` (short), then **PART 1** of `replica/raft.py`.

**Install once:**

```powershell
pip install -r replica\requirements.txt
```

**Run a single replica** (no peers):

```powershell
cd replica
$env:NODE_ID = "node1"; $env:PEERS = ""
python -m uvicorn server:app --port 5001
```

**What you should see:** after about a second, `election timeout -> candidate for term 1` and then
`*** became LEADER ***`. With no peers, 1 vote is already a majority of 1.

**Try it** in a second PowerShell window:

```powershell
Invoke-RestMethod http://localhost:5001/status
Invoke-RestMethod http://localhost:5001/submit -Method Post -ContentType "application/json" -Body '{"command":{"type":"stroke","stroke":{"id":"a"}}}'
Invoke-RestMethod http://localhost:5001/board
```

You can also open **http://localhost:5001/docs**: FastAPI gives you a page to try every endpoint.

Press **Ctrl + C** to stop it.

---

## Step 3 – Three replicas and leader election

**Read:** PART 2 and PART 3 of `raft.py` (`_election_loop`, `start_election`, `handle_request_vote`).

**Run three replicas**, one per PowerShell window (from the project folder):

```powershell
# window 1
cd replica; $env:NODE_ID="node1"; $env:PEERS="http://localhost:5002,http://localhost:5003"; python -m uvicorn server:app --port 5001
# window 2
cd replica; $env:NODE_ID="node2"; $env:PEERS="http://localhost:5001,http://localhost:5003"; python -m uvicorn server:app --port 5002
# window 3
cd replica; $env:NODE_ID="node3"; $env:PEERS="http://localhost:5001,http://localhost:5002"; python -m uvicorn server:app --port 5003
```

**What you should see:** one window prints `became LEADER`, the others print `voted for ...` and `following leader ...`.

**Experiment:** close the leader's window. Within about a second another node becomes leader with a **higher term**.
Start the closed one again: it becomes a follower of the new leader.

> Shortcut: `start-local.ps1` opens these windows for you (Step 5).

---

## Step 4 – Log replication and commits

**Read:** PART 4 of `raft.py` (`_replicate_to`, `_advance_commit_index`, `handle_append_entries`) and `submit` in PART 5.

With the three replicas running, find the leader and send it strokes:

```powershell
Invoke-RestMethod http://localhost:5001/status   # check which one says role = leader
Invoke-RestMethod http://localhost:5001/submit -Method Post -ContentType "application/json" -Body '{"command":{"type":"stroke","stroke":{"id":"hello"}}}'
```

If you send it to a follower you get **409 "not leader"**. That's correct: only the leader accepts writes.

Now look at every node's log. They are identical:

```powershell
Invoke-RestMethod http://localhost:5002/log
Invoke-RestMethod http://localhost:5003/log
```

**Experiment, catch-up:** stop one follower, send 3 strokes to the leader (they still commit: 2 of 3 is a majority),
then start the follower again. Check its `/log`. The leader sent it everything it missed.

How the catch-up works: the leader sends `prev_log_index` and `prev_log_term` ("the entry before these is #5 from term 2").
If the follower doesn't have that entry it says no, and the leader steps back and tries earlier, until they match.

---

## Step 5 – The gateway

**Read:** `gateway/index.js` from top to bottom. Four numbered sections:
leader discovery, forwarding with retries, WebSocket, HTTP API.

```powershell
cd gateway
npm install
node index.js
```

**What you should see:** `leader is now http://localhost:500X`.

Why do we need it? Browsers shouldn't have to know which replica is the leader, and the leader changes.
The gateway asks every replica's `/status` each second, and if forwarding fails it looks again and retries,
so a stroke drawn **during** a failover is still saved.

**From now on** you can start all 4 processes at once:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
```

---

## Step 6 – The frontend

**Read:** `frontend/index.html`, then `frontend/app.js` (three parts: drawing, WebSocket, dashboard).

Open **http://localhost:3000** in two or three tabs and draw.

What happens when you draw one line:

1. `pointerup` → the browser sends `{type: "stroke", stroke}` over the WebSocket.
2. The gateway forwards it to the leader's `/submit`.
3. The leader appends it to its log and sends AppendEntries to followers.
4. When 2 of 3 have it, it's committed and `/submit` returns success.
5. The gateway sends the stroke to every other tab.

Points are stored as fractions (0–1) of the canvas, so tabs of different sizes show the same picture.

---

## Step 7 – Break it on purpose

Use the **Crash / Recover** buttons in the Cluster panel.

| Do this | You should see |
|---|---|
| Crash the **leader** | Within ~1 s another node is leader with a higher term. Drawing keeps working. |
| Recover it | It comes back as a follower; its log/committed numbers catch up. |
| Crash **two** nodes | Drawing shows "Not saved": no majority, so nothing commits. Your stroke disappears. |
| Recover them | Everything works again. All three show the same committed count. |
| Reload a tab | It gets the full board from the leader: committed data survived everything. |

"Crash" makes the node stop answering (like unplugging its network cable) but keeps its memory.
To lose memory too, close the node's window (or `docker stop`) and start it again: it starts empty
and the leader re-sends the whole log.

**Automated version of these experiments:**

```powershell
python tests/test_cluster.py
```

---

## Step 8 – Run it with Docker

**Read:** `replica/Dockerfile`, `gateway/Dockerfile`, `docker-compose.yml`.

Stop everything from the previous steps, make sure **Docker Desktop is running**, then:

```powershell
docker compose up --build
```

Open **http://localhost:3000**. The difference from running locally: each replica is its own container,
and they find each other by name (`http://replica1:5000`) on Docker's private network.

Real crash test, in another window:

```powershell
docker compose stop replica1     # kill a container
docker compose start replica1    # bring it back, empty, and watch it catch up
docker compose logs -f replica2  # watch one node's log
docker compose down              # stop everything
```

---

## Roadmap

- **5-node cluster** – tolerate 2 simultaneous failures (majority of 5 = 3).
- **Undo / redo** – as compensating log entries, so history stays replicated and consistent.
- **Persistence** – save `current_term`, `voted_for` and the log to disk, so a node that restarts
  cannot vote twice in the same term and the board survives a full-cluster restart.
- **Richer dashboard** – leader-change history and per-follower replication lag.
- **Network-partition simulation** – drop traffic between chosen peers to test split-brain safety.

Further reading: the RAFT paper "In Search of an Understandable Consensus Algorithm" (Ongaro & Ousterhout)
and the visual explanation at thesecretlivesofdata.com/raft.
