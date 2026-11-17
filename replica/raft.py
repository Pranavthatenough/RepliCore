"""
raft.py  -  the heart of the project: one RAFT node.

The file is split into 5 parts:

  PART 1  The node's memory (state)
  PART 2  Background loops (election timer + heartbeats)
  PART 3  Leader election   (RequestVote)
  PART 4  Log replication   (AppendEntries)
  PART 5  Client requests, crash / recover, status

Words you will see everywhere:
  term          An "election round" number. It only ever goes up.
                Whoever has the highest term is the most up to date.
  log           An ordered list of entries. Each entry = {"term", "command"}.
                Here a command is "draw this stroke" or "clear the board".
  commit_index  How many entries are SAFE. An entry is safe (committed) once
                a majority of nodes have stored it. Only committed entries
                are shown on the drawing board.
  majority      More than half the nodes. With 3 nodes that is 2.

Log positions are 1-based like in the RAFT paper:
  index 1 is self.log[0], index 2 is self.log[1], and index 0 means "empty".
"""

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

FOLLOWER = "follower"
CANDIDATE = "candidate"
LEADER = "leader"

HEARTBEAT_INTERVAL = 0.15          # leader says "I'm alive" every 150 ms
ELECTION_TIMEOUT = (0.6, 1.2)      # follower waits this long (random) before starting an election
RPC_TIMEOUT = 0.3                  # how long we wait for another node to answer


def log(node_id, message):
    """Print a line with a timestamp so you can follow what happens."""
    print(f"[{time.strftime('%H:%M:%S')}] [{node_id}] {message}", flush=True)


class RaftNode:

    # ------------------------------------------------------------------
    # PART 1  The node's memory (state)
    # ------------------------------------------------------------------
    def __init__(self, node_id, peers):
        self.node_id = node_id          # e.g. "node1"
        self.peers = peers              # URLs of the OTHER nodes

        # Every node starts as a follower in term 0 with an empty log.
        self.role = FOLLOWER
        self.current_term = 0
        self.voted_for = None           # who we voted for in current_term
        self.leader_id = None           # who we think the leader is

        self.log = []                   # list of {"term": int, "command": dict}
        self.commit_index = 0           # entries 1..commit_index are committed

        # Only used while we are leader: what each follower already has.
        self.next_index = {}            # next entry to send to each peer
        self.match_index = {}           # highest entry known stored on each peer

        self.crashed = False            # set by /crash to simulate a dead node

        # Several threads touch this state, so we guard it with one lock.
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=16)   # for parallel network calls
        self.sending_to = set()         # peers we are currently sending to

        self._reset_election_timer()

        threading.Thread(target=self._election_loop, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        log(self.node_id, f"started as follower, peers = {self.peers}")

    # Small helpers -----------------------------------------------------
    def _reset_election_timer(self):
        # Random timeout so nodes don't all start elections at the same moment.
        self.election_deadline = time.time() + random.uniform(*ELECTION_TIMEOUT)

    def _last_log_index(self):
        return len(self.log)

    def _last_log_term(self):
        return self.log[-1]["term"] if self.log else 0

    def _majority(self):
        cluster_size = len(self.peers) + 1          # the peers + me
        return cluster_size // 2 + 1

    def _become_follower(self, term):
        """Step down. Called whenever we see a term bigger than ours."""
        if term > self.current_term:
            self.current_term = term
            self.voted_for = None                   # new term, we haven't voted yet
        if self.role != FOLLOWER:
            log(self.node_id, f"stepping down to follower (term {self.current_term})")
        self.role = FOLLOWER
        self._reset_election_timer()

    def _post(self, peer, path, body):
        """Send a request to another node. Returns its JSON, or None if it didn't answer."""
        try:
            response = requests.post(peer + path, json=body, timeout=RPC_TIMEOUT)
            if response.ok:
                return response.json()
        except requests.RequestException:
            pass
        return None

    # ------------------------------------------------------------------
    # PART 2  Background loops
    # ------------------------------------------------------------------
    def _election_loop(self):
        """Followers and candidates: if the leader goes quiet for too long, start an election."""
        while True:
            time.sleep(0.05)
            with self.lock:
                should_start = (
                    not self.crashed
                    and self.role != LEADER
                    and time.time() >= self.election_deadline
                )
            if should_start:
                self.start_election()

    def _heartbeat_loop(self):
        """Leader only: every HEARTBEAT_INTERVAL send AppendEntries to everybody.
        With no new entries this is just a heartbeat ("I'm still the leader")."""
        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            self.replicate_to_all()

    # ------------------------------------------------------------------
    # PART 3  Leader election
    # ------------------------------------------------------------------
    def start_election(self):
        with self.lock:
            self.role = CANDIDATE
            self.current_term += 1                  # new election round
            self.voted_for = self.node_id           # vote for myself
            self.leader_id = None
            self._reset_election_timer()            # if this election fails, try again later
            term = self.current_term
            request = {
                "term": term,
                "candidate_id": self.node_id,
                "last_log_index": self._last_log_index(),
                "last_log_term": self._last_log_term(),
            }
        log(self.node_id, f"election timeout -> candidate for term {term}")

        # Ask every peer for its vote at the same time (outside the lock!).
        answers = list(self.pool.map(lambda p: self._post(p, "/request-vote", request), self.peers))

        with self.lock:
            # Things may have changed while we waited (e.g. another leader appeared).
            if self.role != CANDIDATE or self.current_term != term:
                return
            votes = 1                               # my own vote
            for answer in answers:
                if answer is None:
                    continue                        # peer is down
                if answer["term"] > self.current_term:
                    self._become_follower(answer["term"])
                    return
                if answer["vote_granted"]:
                    votes += 1

            if votes >= self._majority():
                self._become_leader()
            else:
                log(self.node_id, f"lost election for term {term} ({votes} votes)")

        if self.role == LEADER:
            self.replicate_to_all()                 # announce ourselves right away

    def _become_leader(self):
        self.role = LEADER
        self.leader_id = self.node_id
        for peer in self.peers:
            self.next_index[peer] = self._last_log_index() + 1   # optimistic guess
            self.match_index[peer] = 0
        log(self.node_id, f"*** became LEADER for term {self.current_term} ***")

    def handle_request_vote(self, term, candidate_id, last_log_index, last_log_term):
        """Another node asks: 'will you vote for me?'"""
        with self.lock:
            if term > self.current_term:
                self._become_follower(term)

            # Rule 1: never vote for an old term.
            # Rule 2: only one vote per term.
            # Rule 3: only vote for a candidate whose log is at least as up to date as ours,
            #         so a node with missing data can never become leader.
            candidate_log_ok = (
                last_log_term > self._last_log_term()
                or (last_log_term == self._last_log_term() and last_log_index >= self._last_log_index())
            )
            grant = (
                term == self.current_term
                and self.voted_for in (None, candidate_id)
                and candidate_log_ok
            )
            if grant:
                self.voted_for = candidate_id
                self._reset_election_timer()
                log(self.node_id, f"voted for {candidate_id} in term {term}")
            return {"term": self.current_term, "vote_granted": grant}

    # ------------------------------------------------------------------
    # PART 4  Log replication
    # ------------------------------------------------------------------
    def replicate_to_all(self):
        with self.lock:
            if self.crashed or self.role != LEADER:
                return
            term = self.current_term
            if not self.peers:
                self._advance_commit_index()        # single-node cluster
            for peer in self.peers:
                if peer not in self.sending_to:     # don't pile up requests to a slow peer
                    self.sending_to.add(peer)
                    self.pool.submit(self._replicate_to, peer, term)

    def _replicate_to(self, peer, term):
        """Send one follower the entries it is missing (or an empty heartbeat)."""
        try:
            with self.lock:
                if self.role != LEADER or self.current_term != term:
                    return
                next_idx = self.next_index[peer]
                prev_index = next_idx - 1
                prev_term = self.log[prev_index - 1]["term"] if prev_index > 0 else 0
                entries = self.log[next_idx - 1:next_idx - 1 + 100]   # at most 100 at a time
                request = {
                    "term": term,
                    "leader_id": self.node_id,
                    "prev_log_index": prev_index,     # "the entry just before these is..."
                    "prev_log_term": prev_term,       # "...and it was written in this term"
                    "entries": entries,
                    "leader_commit": self.commit_index,
                }

            answer = self._post(peer, "/append-entries", request)

            with self.lock:
                if answer is None:
                    return                            # peer down, try again next heartbeat
                if answer["term"] > self.current_term:
                    self._become_follower(answer["term"])
                    return
                if self.role != LEADER or self.current_term != term:
                    return
                if answer["success"]:
                    self.match_index[peer] = max(self.match_index[peer], prev_index + len(entries))
                    self.next_index[peer] = self.match_index[peer] + 1
                    self._advance_commit_index()
                else:
                    # Follower's log doesn't match. Step back and try earlier entries.
                    # (The follower tells us how long its log is so we can jump back fast.)
                    self.next_index[peer] = max(1, min(next_idx - 1, answer["last_log_index"] + 1))
        finally:
            with self.lock:
                self.sending_to.discard(peer)

    def _advance_commit_index(self):
        """Leader: an entry is committed once a majority of nodes have it."""
        for index in range(self._last_log_index(), self.commit_index, -1):
            # RAFT rule: only count replicas for entries from OUR term.
            if self.log[index - 1]["term"] != self.current_term:
                break
            copies = 1 + sum(1 for p in self.peers if self.match_index[p] >= index)
            if copies >= self._majority():
                log(self.node_id, f"committed up to entry {index} ({copies} copies)")
                self.commit_index = index
                break

    def handle_append_entries(self, term, leader_id, prev_log_index, prev_log_term, entries, leader_commit):
        """Called on followers by the leader: heartbeat + new entries."""
        with self.lock:
            # A leader from an old term is ignored.
            if term < self.current_term:
                return {"term": self.current_term, "success": False, "last_log_index": self._last_log_index()}

            # A valid leader exists: become / stay follower and reset our timer.
            self._become_follower(term)
            if self.leader_id != leader_id:
                log(self.node_id, f"following leader {leader_id} (term {term})")
            self.leader_id = leader_id

            # Consistency check: do we have the entry right before the new ones?
            if prev_log_index > self._last_log_index():
                return {"term": self.current_term, "success": False, "last_log_index": self._last_log_index()}
            if prev_log_index > 0 and self.log[prev_log_index - 1]["term"] != prev_log_term:
                return {"term": self.current_term, "success": False, "last_log_index": prev_log_index - 1}

            # Add the new entries. If something conflicts, the leader wins: cut our log there.
            index = prev_log_index
            for entry in entries:
                index += 1
                if index <= self._last_log_index():
                    if self.log[index - 1]["term"] == entry["term"]:
                        continue                    # already have it
                    del self.log[index - 1:]
                    log(self.node_id, f"removed conflicting entries from {index}")
                self.log.append(entry)
            if entries:
                log(self.node_id, f"stored entries up to {index}")

            # Leader told us how far it has committed. Follow it (but not past what we have).
            if leader_commit > self.commit_index:
                self.commit_index = min(leader_commit, index)

            return {"term": self.current_term, "success": True, "last_log_index": self._last_log_index()}

    # ------------------------------------------------------------------
    # PART 5  Client requests, crash / recover, status
    # ------------------------------------------------------------------
    def submit(self, command, wait_seconds=2.0):
        """Leader only. Add a command to the log and wait until it is committed.
        Returns True (committed), False (timed out / lost leadership), None (not leader)."""
        with self.lock:
            if self.crashed or self.role != LEADER:
                return None
            self.log.append({"term": self.current_term, "command": command})
            index, term = self._last_log_index(), self.current_term
            log(self.node_id, f"new entry {index}: {command.get('type')}")

        self.replicate_to_all()                     # don't wait for the next heartbeat

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            with self.lock:
                if self.commit_index >= index and self.log[index - 1]["term"] == term:
                    return True
                if self.role != LEADER or self.current_term != term:
                    return False
            time.sleep(0.02)
        return False

    def board(self):
        """Replay the committed log to get what the drawing board shows."""
        with self.lock:
            committed = self.log[:self.commit_index]
        strokes = []
        for entry in committed:
            command = entry["command"]
            if command["type"] == "stroke":
                strokes.append(command["stroke"])
            elif command["type"] == "clear":
                strokes = []
        return strokes

    def crash(self):
        """Simulate a crash: the node stops answering and stops doing anything."""
        with self.lock:
            self.crashed = True
            self.role = FOLLOWER
            self.leader_id = None
        log(self.node_id, "CRASHED (simulated)")

    def recover(self):
        """Come back. The log we had is kept (like reading it back from disk),
        and the leader will send us whatever we missed."""
        with self.lock:
            self.crashed = False
            self._reset_election_timer()
        log(self.node_id, "RECOVERED, waiting to hear from a leader")

    def status(self):
        with self.lock:
            return {
                "node_id": self.node_id,
                "role": self.role,
                "term": self.current_term,
                "leader": self.leader_id,
                "log_length": self._last_log_index(),
                "commit_index": self.commit_index,
                "crashed": self.crashed,
            }
