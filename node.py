from __future__ import annotations

import hashlib
import random
import time
from threading import Lock
from typing import Optional, Tuple

from core.state import PBFTEntry, PBFTState
from rpc import pbft_pb2


class PBFTNode:
    def __init__(self, node_id: int, peers: list[int], rpc_clients):
        self.state = PBFTState(node_id, peers)
        self.rpc_clients = rpc_clients
        self._lock = Lock()

    def start(self):
        state = self.state
        # If this process was restarted while the cluster already moved to a higher view,
        # sync up so we don't reject messages forever.
        self._sync_view_from_peers(best_effort=True)
        print(f"[PBFT Node {state.node_id}] starting")
        print(
            f"[PBFT Node {state.node_id}] role={state.role} view={state.view} primary={state.primary_id} f={state.f} n={state.n}"
        )

    # ============================
    # View / failover helpers
    # ============================
    def _sync_view_from_peers(self, best_effort: bool = True) -> None:
        state = self.state
        max_view = state.view
        for pid, client in list(self.rpc_clients.items()):
            try:
                status = client.get_status()
                if int(status.view) > max_view:
                    max_view = int(status.view)
            except Exception:
                continue

        if max_view > state.view:
            with self._lock:
                state.view = max_view

    def _set_view(self, new_view: int, reason: str = "") -> bool:
        state = self.state
        new_view = int(new_view)
        if new_view <= state.view:
            return False
        with self._lock:
            if new_view <= state.view:
                return False
            state.view = new_view
        print(
            f"[PBFT {state.node_id}] VIEW -> {state.view} primary={state.primary_id} reason={reason}"
        )
        return True

    def _broadcast_set_view(self, new_view: int, reason: str) -> None:
        state = self.state
        req = pbft_pb2.SetViewRequest(
            view=int(new_view), sender_id=int(state.node_id), reason=str(reason)
        )
        for pid, client in list(self.rpc_clients.items()):
            try:
                client.set_view(req, timeout=0.5)
            except Exception:
                pass

    def _ensure_live_primary(self, max_hops: Optional[int] = None) -> bool:
        state = self.state
        hops = int(max_hops if max_hops is not None else state.n)
        for _ in range(max(1, hops)):
            primary_id = int(state.primary_id)
            if primary_id == int(state.node_id):
                return True

            client = self.rpc_clients.get(primary_id)
            if client is not None:
                try:
                    client.ping(timeout=0.4)
                    return True
                except Exception:
                    pass

            # primary unreachable -> bump view
            bumped_to = int(state.view) + 1
            changed = self._set_view(
                bumped_to, reason=f"failover: primary {primary_id} unreachable"
            )
            if changed:
                self._broadcast_set_view(
                    bumped_to, reason=f"failover: primary {primary_id} unreachable"
                )

        return int(state.primary_id) == int(state.node_id)

    # ============================
    # PBFT helpers
    # ============================
    def _digest(self, request: pbft_pb2.ClientRequest) -> str:
        h = hashlib.sha256()
        h.update(request.client_id.encode("utf-8"))
        h.update(b"|")
        h.update(request.request_id.encode("utf-8"))
        h.update(b"|")
        h.update(request.payload.encode("utf-8"))
        return h.hexdigest()

    def _entry_key(self, view: int, seq: int) -> Tuple[int, int]:
        return (view, seq)

    def _pending_key(self, view: int, seq: int, digest: str) -> Tuple[int, int, str]:
        return (int(view), int(seq), str(digest))

    def _maybe_corrupt_digest(self, digest: str) -> str:
        # Simulate Byzantine behavior by sending an invalid digest.
        # Keep it deterministic and obviously mismatching.
        if not self.state.byzantine:
            return digest
        return f"{digest}:byz"

    def _short(self, s: str, n: int = 12) -> str:
        s = str(s)
        return s if len(s) <= n else f"{s[:n]}..."

    def _byz_make_chaos_pre_prepare(
        self,
        *,
        base_req: pbft_pb2.ClientRequest,
        view: int,
        seq: int,
        primary_id: int,
        peer_id: int,
    ) -> pbft_pb2.PrePrepareRequest:
        """Generate a per-peer chaotic PRE-PREPARE.

        Two chaos modes:
        - wrong digest for the real request (will be rejected)
        - mutated payload with matching digest (replica may accept, but peers won't agree)
        """
        chaos_mode = random.choice(["wrong_digest", "mutated_payload"])

        if chaos_mode == "wrong_digest":
            digest = self._maybe_corrupt_digest(self._digest(base_req))
            return pbft_pb2.PrePrepareRequest(
                view=int(view),
                seq=int(seq),
                digest=str(digest),
                primary_id=int(primary_id),
                request=base_req,
            )

        # mutated_payload
        mutated_payload = (
            f"{base_req.payload}|BYZ:{peer_id}:{random.randint(1, 1_000_000)}"
        )
        mutated_req = pbft_pb2.ClientRequest(
            client_id=base_req.client_id,
            request_id=base_req.request_id,
            timestamp_ms=base_req.timestamp_ms,
            payload=mutated_payload,
            forwarded=base_req.forwarded,
        )
        digest = self._digest(mutated_req)
        return pbft_pb2.PrePrepareRequest(
            view=int(view),
            seq=int(seq),
            digest=str(digest),
            primary_id=int(primary_id),
            request=mutated_req,
        )

    def _multicast_prepare(self, view: int, seq: int, digest: str) -> None:
        state = self.state
        # Per current simulation rules: the primary must NOT send PREPARE.
        # Only replicas send PREPARE after receiving PRE-PREPARE.
        if int(state.node_id) == int(state.primary_id):
            return
        out_digest = self._maybe_corrupt_digest(str(digest))
        if state.byzantine and out_digest != str(digest):
            print(
                f"[PBFT {state.node_id}] BYZ SEND PREPARE view={view} seq={seq} digest={out_digest} (was {digest})"
            )
        prepare = pbft_pb2.PrepareRequest(
            view=int(view),
            seq=int(seq),
            digest=out_digest,
            replica_id=int(state.node_id),
        )

        for peer in state.peers:
            try:
                print(
                    f"[PBFT {state.node_id}] SEND PREPARE -> {peer} view={view} seq={seq}"
                )
                ack = self.rpc_clients[peer].prepare(prepare, timeout=0.5)
                if hasattr(ack, "ok") and (not ack.ok):
                    print(
                        f"[PBFT {state.node_id}] PREPARE rejected by {peer}: {getattr(ack, 'error', '')} digest={prepare.digest}"
                    )
            except Exception:
                pass

        # Count our own PREPARE vote locally
        self.on_prepare(prepare)

    # ============================
    # RPC handlers (called by rpc/server.py)
    # ============================
    def on_client_request(
        self, req: pbft_pb2.ClientRequest, timeout_s: float = 30.0
    ) -> pbft_pb2.ClientReply:
        state = self.state

        if not state.alive:
            return pbft_pb2.ClientReply(
                client_id=req.client_id,
                request_id=req.request_id,
                replica_id=state.node_id,
                view=state.view,
                seq=0,
                committed=False,
                result="",
                error="node is not alive",
            )
        print("=" * 37)

        if state.node_id != state.primary_id:
            print(
                f"[PBFT {state.node_id}] RECV REQUEST client={req.client_id} rid={req.request_id} (not primary, primary_id={state.primary_id})"
            )
            # Minimal forwarding for convenience (avoid forwarding loops)
            if req.forwarded:
                return pbft_pb2.ClientReply(
                    client_id=req.client_id,
                    request_id=req.request_id,
                    replica_id=state.node_id,
                    view=state.view,
                    seq=0,
                    committed=False,
                    result="",
                    error=f"not primary (primary_id={state.primary_id})",
                )

            # If current primary is down, try a simplified failover (bump view -> new primary).
            self._ensure_live_primary()

            # If we became the new primary after a view bump, handle the request locally.
            if state.node_id == state.primary_id:
                return self.on_client_request(req, timeout_s=timeout_s)

            # Re-check role after possible view change
            if state.node_id != state.primary_id:
                if state.primary_id in self.rpc_clients:
                    fwd = pbft_pb2.ClientRequest(
                        client_id=req.client_id,
                        request_id=req.request_id,
                        timestamp_ms=req.timestamp_ms,
                        payload=req.payload,
                        forwarded=True,
                    )
                    try:
                        print(
                            f"[PBFT {state.node_id}] SEND REQUEST -> primary {state.primary_id}"
                        )
                        return self.rpc_clients[state.primary_id].client_request(
                            fwd, timeout=timeout_s
                        )
                    except Exception as e:
                        return pbft_pb2.ClientReply(
                            client_id=req.client_id,
                            request_id=req.request_id,
                            replica_id=state.node_id,
                            view=state.view,
                            seq=0,
                            committed=False,
                            result="",
                            error=f"forward to primary failed: {e}",
                        )

            return pbft_pb2.ClientReply(
                client_id=req.client_id,
                request_id=req.request_id,
                replica_id=state.node_id,
                view=state.view,
                seq=0,
                committed=False,
                result="",
                error=f"not primary (primary_id={state.primary_id})",
            )

        # Primary path
        with self._lock:
            view = state.view
            seq = state.next_seq
            state.next_seq += 1
            digest = self._digest(req)

            key = self._entry_key(view, seq)
            entry = PBFTEntry(
                view=view,
                seq=seq,
                digest=digest,
                client_id=req.client_id,
                request_id=req.request_id,
                payload=req.payload,
            )
            state.log[key] = entry

        print(
            f"[PBFT {state.node_id}] REQUEST  client={req.client_id} rid={req.request_id} -> view={view} seq={seq} msg={self._short(req.payload, 48)!r}"
        )

        # PRE-PREPARE
        pre = pbft_pb2.PrePrepareRequest(
            view=view,
            seq=seq,
            digest=digest,
            primary_id=state.node_id,
            request=req,
        )
        # Per current simulation rules: primary only sends PRE-PREPARE to replicas.
        # No local PRE-PREPARE processing and no PREPARE from primary.

        # Byzantine primary: send chaotic PRE-PREPARE messages per peer (do NOT send the correct one).
        if state.byzantine:
            for peer in state.peers:
                try:
                    chaos_pre = self._byz_make_chaos_pre_prepare(
                        base_req=req,
                        view=view,
                        seq=seq,
                        primary_id=int(state.node_id),
                        peer_id=int(peer),
                    )
                    print(
                        f"[PBFT {state.node_id}] BYZ SEND PRE-PREPARE -> {peer} view={view} seq={seq} digest={str(chaos_pre.digest)[:8]}... msg={self._short(chaos_pre.request.payload, 48)!r}"
                    )
                    self.rpc_clients[peer].pre_prepare(chaos_pre, timeout=0.5)
                except Exception:
                    pass

            return pbft_pb2.ClientReply(
                client_id=req.client_id,
                request_id=req.request_id,
                replica_id=state.node_id,
                view=view,
                seq=seq,
                committed=False,
                result="",
                error="byzantine primary: sent chaotic PRE-PREPARE (no commit expected)",
            )

        # PBFT: primary multicasts PRE-PREPARE to replicas
        for peer in state.peers:
            try:
                print(
                    f"[PBFT {state.node_id}] SEND PRE-PREPARE -> {peer} view={view} seq={seq} digest={digest[:8]}... msg={self._short(req.payload, 48)!r}"
                )
                self.rpc_clients[peer].pre_prepare(pre, timeout=0.5)
            except Exception:
                pass

        # Wait until committed (primary returns a single reply)
        entry = state.log[self._entry_key(view, seq)]
        deadline = time.time() + timeout_s
        with entry.done:
            while not entry.executed and time.time() < deadline:
                remaining = max(0.05, deadline - time.time())
                entry.done.wait(timeout=remaining)

        with self._lock:
            entry = state.log.get(self._entry_key(view, seq))

        if entry is None:
            return pbft_pb2.ClientReply(
                client_id=req.client_id,
                request_id=req.request_id,
                replica_id=state.node_id,
                view=view,
                seq=seq,
                committed=False,
                result="",
                error="request entry missing",
            )

        return pbft_pb2.ClientReply(
            client_id=entry.client_id,
            request_id=entry.request_id,
            replica_id=state.node_id,
            view=entry.view,
            seq=entry.seq,
            committed=bool(entry.committed),
            result=entry.result or "",
            error=entry.error or "",
        )

    def on_pre_prepare(
        self, req: pbft_pb2.PrePrepareRequest, broadcast_prepare: bool = True
    ) -> pbft_pb2.Ack:
        state = self.state
        if not state.alive:
            return pbft_pb2.Ack(ok=False, error="node is not alive")
        if int(req.view) > int(state.view):
            self._set_view(int(req.view), reason="observed higher view in PRE-PREPARE")
        if req.view != state.view:
            return pbft_pb2.Ack(ok=False, error="wrong view")
        if req.primary_id != state.primary_id:
            return pbft_pb2.Ack(ok=False, error="wrong primary")

        digest = self._digest(req.request)
        if digest != req.digest:
            print(
                f"[PBFT {state.node_id}] REJECT PRE-PREPARE from {req.primary_id} view={req.view} seq={req.seq}: digest mismatch recv={req.digest} expected={digest} msg={self._short(req.request.payload, 48)!r}"
            )

            # Strong evidence of a faulty primary in this simplified simulator:
            # bump view and broadcast so the cluster rotates to a new primary.
            bumped_to = int(state.view) + 1
            changed = self._set_view(
                bumped_to,
                reason=f"suspect primary {state.primary_id}: PRE-PREPARE digest mismatch",
            )
            if changed:
                self._broadcast_set_view(
                    bumped_to,
                    reason=f"suspect primary {state.primary_id}: PRE-PREPARE digest mismatch",
                )

            return pbft_pb2.Ack(ok=False, error="digest mismatch")

        key = self._entry_key(req.view, int(req.seq))
        pkey = self._pending_key(req.view, int(req.seq), req.digest)

        created_entry = False

        with self._lock:
            if key not in state.log:
                state.log[key] = PBFTEntry(
                    view=req.view,
                    seq=int(req.seq),
                    digest=req.digest,
                    client_id=req.request.client_id,
                    request_id=req.request.request_id,
                    payload=req.request.payload,
                )
                created_entry = True
            entry = state.log[key]

            # Apply any out-of-order PREPARE/COMMIT that arrived before this PRE-PREPARE
            pending_prepares = state.pending_prepares.pop(pkey, set())
            if pending_prepares:
                entry.prepares.update(pending_prepares)

            pending_commits = state.pending_commits.pop(pkey, set())
            if pending_commits:
                entry.commits.update(pending_commits)

        if created_entry:
            print("=" * 37)

        if state.node_id == int(req.primary_id):
            print(
                f"[PBFT {state.node_id}] LOCAL PRE-PREPARE view={req.view} seq={req.seq} digest={req.digest[:8]}... msg={self._short(req.request.payload, 48)!r}"
            )
        else:
            print(
                f"[PBFT {state.node_id}] RECV PRE-PREPARE from {req.primary_id} view={req.view} seq={req.seq} digest={req.digest[:8]}... msg={self._short(req.request.payload, 48)!r}"
            )

        if broadcast_prepare:
            self._multicast_prepare(
                view=int(req.view), seq=int(req.seq), digest=str(req.digest)
            )

        return pbft_pb2.Ack(ok=True, error="")

    def on_prepare(self, req: pbft_pb2.PrepareRequest) -> pbft_pb2.Ack:
        state = self.state
        if not state.alive:
            return pbft_pb2.Ack(ok=False, error="node is not alive")
        if int(req.view) > int(state.view):
            self._set_view(int(req.view), reason="observed higher view in PREPARE")
        if req.view != state.view:
            return pbft_pb2.Ack(ok=False, error="wrong view")

        key = self._entry_key(req.view, int(req.seq))

        if int(req.replica_id) == state.node_id:
            print(f"[PBFT {state.node_id}] LOCAL PREPARE view={req.view} seq={req.seq}")
        else:
            print(
                f"[PBFT {state.node_id}] RECV PREPARE from {req.replica_id} view={req.view} seq={req.seq}"
            )

        with self._lock:
            entry = state.log.get(key)
            if entry is None:
                pkey = self._pending_key(req.view, int(req.seq), req.digest)
                first_time = pkey not in state.pending_prepares
                state.pending_prepares.setdefault(pkey, set()).add(int(req.replica_id))
                # if first_time:
                #     print("=" * 37)
                print(
                    f"[PBFT {state.node_id}] BUFFER PREPARE from {req.replica_id} view={req.view} seq={req.seq} (no pre-prepare yet)"
                )
                return pbft_pb2.Ack(ok=True, error="buffered")
            if entry.executed:
                # Late/duplicate message after execution; ignore.
                return pbft_pb2.Ack(ok=True, error="ignored (already executed)")
            if entry.digest != req.digest:
                # Conflicting digests for the same (view,seq) are what you see when a byzantine
                # primary sends different PRE-PREPAREs. Accumulate evidence and then trigger a
                # simplified view change.
                conflict_set = state.conflicting_prepares.setdefault(key, set())
                conflict_set.add(int(req.replica_id))
                conflicts = len(conflict_set)
                print(
                    f"[PBFT {state.node_id}] REJECT PREPARE from {req.replica_id} view={req.view} seq={req.seq}: digest mismatch recv={req.digest} expected={entry.digest} conflicts={conflicts}/{state.f + 1}"
                )

                # Threshold: f+1 distinct conflicting senders (at least one honest) -> suspect primary.
                if conflicts >= (state.f + 1):
                    bumped_to = int(state.view) + 1
                    reason = f"suspect primary {state.primary_id}: conflicting PREPARE digests for view={req.view} seq={req.seq}"
                    changed = self._set_view(bumped_to, reason=reason)
                    if changed:
                        self._broadcast_set_view(bumped_to, reason=reason)

                return pbft_pb2.Ack(ok=False, error="digest mismatch")

            entry.prepares.add(int(req.replica_id))
            prepares_count = len(entry.prepares)
            became_prepared = False
            if not entry.prepared and prepares_count >= state.quorum_prepare:
                entry.prepared = True
                became_prepared = True

        if became_prepared:
            print(
                f"[PBFT {state.node_id}] PREPARED view={req.view} seq={req.seq} prepares={prepares_count}/{state.quorum_prepare}"
            )

            commit = pbft_pb2.CommitRequest(
                view=req.view,
                seq=req.seq,
                digest=self._maybe_corrupt_digest(req.digest),
                replica_id=state.node_id,
            )
            if state.byzantine and str(commit.digest) != str(req.digest):
                print(
                    f"[PBFT {state.node_id}] BYZ SEND COMMIT view={req.view} seq={req.seq} digest={commit.digest} (was {req.digest})"
                )
            # PBFT: once PREPARED, multicast COMMIT (do not wait to be committed).
            for peer in state.peers:
                try:
                    print(
                        f"[PBFT {state.node_id}] SEND COMMIT -> {peer} view={req.view} seq={req.seq}"
                    )
                    ack = self.rpc_clients[peer].commit(commit, timeout=0.5)
                    if hasattr(ack, "ok") and (not ack.ok):
                        print(
                            f"[PBFT {state.node_id}] COMMIT rejected by {peer}: {getattr(ack, 'error', '')} digest={commit.digest}"
                        )
                except Exception:
                    pass

            # Count our own COMMIT vote locally
            self.on_commit(commit)

        return pbft_pb2.Ack(ok=True, error="")

    def on_commit(self, req: pbft_pb2.CommitRequest) -> pbft_pb2.Ack:
        state = self.state
        if not state.alive:
            return pbft_pb2.Ack(ok=False, error="node is not alive")
        if int(req.view) > int(state.view):
            self._set_view(int(req.view), reason="observed higher view in COMMIT")
        if req.view != state.view:
            return pbft_pb2.Ack(ok=False, error="wrong view")

        key = self._entry_key(req.view, int(req.seq))

        if int(req.replica_id) == state.node_id:
            print(f"[PBFT {state.node_id}] LOCAL COMMIT view={req.view} seq={req.seq}")
        else:
            print(
                f"[PBFT {state.node_id}] RECV COMMIT from {req.replica_id} view={req.view} seq={req.seq}"
            )

        with self._lock:
            entry = state.log.get(key)
            if entry is None:
                pkey = self._pending_key(req.view, int(req.seq), req.digest)
                first_time = pkey not in state.pending_commits
                state.pending_commits.setdefault(pkey, set()).add(int(req.replica_id))
                # if first_time:
                #     print("=" * 37)
                print(
                    f"[PBFT {state.node_id}] BUFFER COMMIT from {req.replica_id} view={req.view} seq={req.seq} (no pre-prepare yet)"
                )
                return pbft_pb2.Ack(ok=True, error="buffered")
            if entry.executed:
                return pbft_pb2.Ack(ok=True, error="ignored (already executed)")
            if entry.digest != req.digest:
                print(
                    f"[PBFT {state.node_id}] REJECT COMMIT from {req.replica_id} view={req.view} seq={req.seq}: digest mismatch recv={req.digest} expected={entry.digest}"
                )
                return pbft_pb2.Ack(ok=False, error="digest mismatch")

            entry.commits.add(int(req.replica_id))
            commits_count = len(entry.commits)
            became_committed = False
            # Only move to COMMITTED after PREPARED (PBFT ordering)
            if (
                entry.prepared
                and (not entry.committed)
                and commits_count >= state.quorum_commit
            ):
                entry.committed = True
                became_committed = True

        if became_committed:
            print(
                f"[PBFT {state.node_id}] COMMITTED view={req.view} seq={req.seq} commits={commits_count}/{state.quorum_commit}"
            )
            self._execute(entry)

        return pbft_pb2.Ack(ok=True, error="")

    def _execute(self, entry: PBFTEntry) -> None:
        state = self.state
        with self._lock:
            if entry.executed:
                return
            # Minimal deterministic "execution": echo payload
            entry.result = entry.payload
            entry.executed = True
            entry.error = ""
        print(
            f"[PBFT {state.node_id}] REPLY    view={entry.view} seq={entry.seq} client={entry.client_id} rid={entry.request_id} result={entry.result!r}"
        )

        with entry.done:
            entry.done.notify_all()

    # ============================
    # RPC handler: SetView
    # ============================
    def on_set_view(self, req: pbft_pb2.SetViewRequest) -> pbft_pb2.Ack:
        state = self.state
        if not state.alive:
            return pbft_pb2.Ack(ok=False, error="node is not alive")

        changed = self._set_view(
            int(req.view), reason=f"set by {req.sender_id}: {req.reason}"
        )
        return pbft_pb2.Ack(ok=True, error="" if changed else "ignored (not higher)")
