"""Cluster process manager.

On Windows we spawn each node in a new console window.
On Linux/macOS we try to open a new terminal emulator window when a GUI is available.
If no GUI terminal is available (e.g. headless server), we fall back to running the
node process in the background.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time


class ClusterManager:
    def __init__(self):
        self.nodes = []  # [{id, port, process}]
        self.running = False
        self.last_error = ""
        self._pid_dir = os.path.join(os.getcwd(), ".pbft_pids")

    def is_running(self):
        return self.running

    def validate_node_count(self, n: int) -> tuple[bool, int]:
        # PBFT classic requirement: n = 3f + 1
        if n <= 0:
            return False, 0
        f = (n - 1) // 3
        ok = (3 * f + 1) == n
        return ok, f

    def resize(self, n):
        if self.running:
            return

        ok, f = self.validate_node_count(int(n))
        if not ok:
            self.last_error = (
                f"Invalid PBFT node count n={n}. Must be n = 3f + 1 (e.g. 4, 7, 10)."
            )
            self.nodes = []
            return

        self.last_error = ""
        self.nodes = []
        base_port = 5000
        for i in range(n):
            self.nodes.append(
                {
                    "id": i + 1,
                    "port": base_port + i + 1,
                    "process": None,
                    "byzantine": False,
                }
            )

    def _peer_str(self) -> str:
        peer_args = []
        for node in self.nodes:
            peer_args.append(f"{node['id']}@localhost:{node['port']}")
        return ",".join(peer_args)

    def _build_node_argv(self, node: dict) -> list[str]:
        argv = [
            sys.executable,
            "run_node.py",
            "--id",
            str(node["id"]),
            "--port",
            str(node["port"]),
            "--peers",
            self._peer_str(),
        ]
        if node.get("byzantine"):
            argv.append("--byzantine")
        return argv

    def _has_gui(self) -> bool:
        # Common display env vars across X11/Wayland.
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    def _quote_argv_for_shell(self, argv: list[str]) -> str:
        return " ".join(shlex.quote(a) for a in argv)

    def _pidfile_for_node(self, node_id: int) -> str:
        os.makedirs(self._pid_dir, exist_ok=True)
        return os.path.join(self._pid_dir, f"node-{int(node_id)}.pid")

    def _read_pidfile(self, pidfile: str) -> int | None:
        try:
            with open(pidfile, "r", encoding="utf-8") as f:
                raw = f.read().strip()
            return int(raw) if raw else None
        except Exception:
            return None

    def _kill_pid(self, pid: int, timeout_s: float = 2.0) -> None:
        # Terminate then force kill.
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception:
            return

        deadline = time.time() + float(timeout_s)
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                return

        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    def _launch_in_new_terminal(
        self, title: str, argv: list[str], pidfile: str
    ) -> subprocess.Popen | None:
        """Best-effort: open a new terminal window and run argv.

        Returns a Popen handle for the terminal emulator, or None if we couldn't.
        """
        if not self._has_gui():
            return None

        cmdline = self._quote_argv_for_shell(argv)
        pidfile_q = shlex.quote(pidfile)

        # Run node as a child process, record its PID, and wait for it.
        # When the node is killed, `wait` returns and the terminal closes.
        bash_cmd = f"({cmdline}) & echo $! > {pidfile_q}; wait $!"

        # Prefer explicit terminal emulators (Ubuntu commonly has gnome-terminal).
        candidates: list[tuple[str, list[str]]] = []

        if shutil.which("gnome-terminal"):
            candidates.append(
                (
                    "gnome-terminal",
                    [
                        "gnome-terminal",
                        "--wait",
                        "--title",
                        title,
                        "--",
                        "bash",
                        "-lc",
                        bash_cmd,
                    ],
                )
            )

        if shutil.which("konsole"):
            candidates.append(
                (
                    "konsole",
                    [
                        "konsole",
                        "--nofork",
                        "--new-window",
                        "-p",
                        f"tabtitle={title}",
                        "-e",
                        "bash",
                        "-lc",
                        bash_cmd,
                    ],
                )
            )

        if shutil.which("xfce4-terminal"):
            candidates.append(
                (
                    "xfce4-terminal",
                    [
                        "xfce4-terminal",
                        "--disable-server",
                        "--title",
                        title,
                        "--command",
                        f"bash -lc {shlex.quote(bash_cmd)}",
                    ],
                )
            )

        if shutil.which("xterm"):
            candidates.append(
                (
                    "xterm",
                    ["xterm", "-T", title, "-e", "bash", "-lc", bash_cmd],
                )
            )

        # Debian/Ubuntu alternative that usually exists if *any* terminal does.
        if shutil.which("x-terminal-emulator"):
            candidates.append(
                (
                    "x-terminal-emulator",
                    ["x-terminal-emulator", "-e", "bash", "-lc", bash_cmd],
                )
            )

        for name, terminal_cmd in candidates:
            try:
                return subprocess.Popen(terminal_cmd)
            except Exception as e:
                self.last_error = f"Failed to open terminal via {name}: {e}"
                continue

        return None

    def start_node(self, node_id: int):
        node = next((n for n in self.nodes if int(n.get("id")) == int(node_id)), None)
        if node is None:
            return
        if node.get("process"):
            return

        title = f"PBFT-Node-{node['id']}"
        argv = self._build_node_argv(node)
        pidfile = self._pidfile_for_node(int(node["id"]))
        node["pidfile"] = pidfile

        # Windows: open a new console window.
        if os.name == "nt":
            cmdline = subprocess.list2cmdline(argv)
            full_cmd = f"cmd.exe /k title {title} && {cmdline}"
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            node["process"] = subprocess.Popen(full_cmd, creationflags=creationflags)
        else:
            # Linux/macOS: try to open a new terminal window; fall back to background.
            proc = self._launch_in_new_terminal(title=title, argv=argv, pidfile=pidfile)
            if proc is None:
                node["process"] = subprocess.Popen(argv, start_new_session=True)
            else:
                node["process"] = proc

        # Update global running flag
        self.running = any(n.get("process") for n in self.nodes)

    def stop_node(self, node_id: int):
        node = next((n for n in self.nodes if int(n.get("id")) == int(node_id)), None)
        if node is None:
            return

        proc = node.get("process")
        if not proc:
            return

        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                # Kill the actual node process (so the terminal command ends and the window closes).
                pidfile = node.get("pidfile")
                if isinstance(pidfile, str) and pidfile:
                    pid = self._read_pidfile(pidfile)
                    if isinstance(pid, int) and pid > 1:
                        self._kill_pid(pid)

                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as e:
            print(f"Failed to stop node {node['id']}: {e}")

        node["process"] = None
        node.pop("pidfile", None)
        self.running = any(n.get("process") for n in self.nodes)

    def start_all(self):
        if self.running:
            return

        ok, _ = self.validate_node_count(len(self.nodes))
        if not ok:
            self.last_error = f"Invalid PBFT node count n={len(self.nodes)}. Must be n = 3f + 1 (e.g. 4, 7, 10)."
            return

        self.last_error = ""

        for node in self.nodes:
            self.start_node(node["id"])

    def stop_all(self):
        for node in self.nodes:
            proc = node["process"]
            if proc:
                try:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    else:
                        pidfile = node.get("pidfile")
                        if isinstance(pidfile, str) and pidfile:
                            pid = self._read_pidfile(pidfile)
                            if isinstance(pid, int) and pid > 1:
                                self._kill_pid(pid)

                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                except Exception as e:
                    print(f"Failed to stop node {node['id']}: {e}")

                node["process"] = None
                node.pop("pidfile", None)

        self.running = False
