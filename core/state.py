from __future__ import annotations

from dataclasses import dataclass, field
from threading import Condition
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class PBFTEntry:
    view: int
    seq: int
    digest: str
    client_id: str
    request_id: str
    payload: str

    prepares: Set[int] = field(default_factory=set)
    commits: Set[int] = field(default_factory=set)

    prepared: bool = False
    committed: bool = False
    executed: bool = False

    result: Optional[str] = None
    error: Optional[str] = None

    done: Condition = field(default_factory=Condition)


class PBFTState:
    def __init__(self, node_id: int, peers: List[int]):
        self.node_id = node_id
        self.peers = peers
        self.alive = True

        # Fault simulation
        # - alive=False: crash/stop participating (handlers return errors)
        # - byzantine=True: still alive but can send malformed protocol messages
        self.byzantine = False

        # PBFT metadata
        self.view = 0
        self.next_seq = 1

        # (view, seq) -> entry
        self.log: Dict[Tuple[int, int], PBFTEntry] = {}

        # Buffer messages that can arrive before PRE-PREPARE due to network reordering
        # Keyed by (view, seq, digest)
        self.pending_prepares: Dict[Tuple[int, int, str], Set[int]] = {}
        self.pending_commits: Dict[Tuple[int, int, str], Set[int]] = {}

        # Evidence of inconsistent PREPAREs for an already-known (view, seq).
        # Used to trigger a simplified view change when the primary appears faulty.
        # Keyed by (view, seq) -> set(replica_id) that sent a conflicting digest.
        self.conflicting_prepares: Dict[Tuple[int, int], Set[int]] = {}

    @property
    def replica_ids(self) -> List[int]:
        return sorted([self.node_id] + list(self.peers))

    @property
    def n(self) -> int:
        return len(self.replica_ids)

    @property
    def f(self) -> int:
        # tolerate f Byzantine faults: n >= 3f + 1
        return max(0, (self.n - 1) // 3)

    @property
    def primary_id(self) -> int:
        ids = self.replica_ids
        if not ids:
            return self.node_id
        return ids[self.view % len(ids)]

    @property
    def role(self) -> str:
        return "Primary" if self.node_id == self.primary_id else "Replica"

    @property
    def quorum_prepare(self) -> int:
        return 2 * self.f

    @property
    def quorum_commit(self) -> int:
        # committed requires 2f+1 commits
        return 2 * self.f + 1
