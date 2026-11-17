"""
tests/test_cluster.py  -  starts 3 replicas by itself and checks the RAFT rules.

Run from the project folder (no Docker needed):
    python tests/test_cluster.py

It uses ports 6001-6003 so it does not clash with a running cluster.
"""

import os
import subprocess
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTS = [6001, 6002, 6003]
URLS = [f"http://localhost:{p}" for p in PORTS]


def start_cluster():
    processes = []
    for i, port in enumerate(PORTS):
        env = dict(os.environ, NODE_ID=f"node{i + 1}",
                   PEERS=",".join(u for j, u in enumerate(URLS) if j != i))
        processes.append(subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--port", str(port), "--log-level", "warning"],
            cwd=os.path.join(ROOT, "replica"), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
    return processes


def status(url):
    try:
        return requests.get(url + "/status", timeout=1).json()
    except requests.RequestException:
        return None


def wait_for_leader(exclude=(), seconds=6):
    deadline = time.time() + seconds
    while time.time() < deadline:
        leaders = [u for u in URLS if u not in exclude and (s := status(u)) and s["role"] == "leader" and not s["crashed"]]
        if len(leaders) == 1:
            return leaders[0]
        time.sleep(0.2)
    raise AssertionError("no single leader was elected")


def submit(url, stroke_id):
    return requests.post(url + "/submit", json={"command": {"type": "stroke", "stroke": {"id": stroke_id}}}, timeout=4)


def board_ids(url):
    return [s["id"] for s in requests.get(url + "/board", timeout=1).json()["strokes"]]


def check(name, condition):
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        raise SystemExit(1)


def main():
    processes = start_cluster()
    try:
        leader = wait_for_leader()
        check("1. exactly one leader is elected", True)

        followers = [u for u in URLS if u != leader]
        check("2. a follower refuses client writes", submit(followers[0], "x").status_code == 409)

        for i in range(3):
            check(f"3.{i + 1} leader commits stroke s{i}", submit(leader, f"s{i}").ok)
        time.sleep(0.5)
        check("4. every replica has the same board", all(board_ids(u) == ["s0", "s1", "s2"] for u in URLS))

        old_term = status(leader)["term"]
        requests.post(leader + "/crash", timeout=1)
        new_leader = wait_for_leader(exclude=[leader])
        check("5. a new leader is elected after the leader crashes", new_leader != leader)
        check("6. the new leader has a higher term", status(new_leader)["term"] > old_term)
        check("7. no committed data was lost", board_ids(new_leader) == ["s0", "s1", "s2"])
        check("8. the cluster still accepts writes with 2 of 3 nodes", submit(new_leader, "s3").ok)

        requests.post(leader + "/recover", timeout=1)
        time.sleep(1.5)
        check("9. the recovered node catches up", board_ids(leader) == ["s0", "s1", "s2", "s3"])

        third = [u for u in URLS if u not in (leader, new_leader)][0]
        requests.post(third + "/crash", timeout=1)
        requests.post(leader + "/crash", timeout=1)
        check("10. with only 1 of 3 nodes alive nothing can be committed", not submit(new_leader, "lost").ok)

        print("\nAll checks passed.")
    finally:
        for p in processes:
            p.terminate()


if __name__ == "__main__":
    main()
