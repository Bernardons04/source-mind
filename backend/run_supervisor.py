"""
Production Process Supervisor for SourceMind Backend on Render.

Manages four independent processes in a single container:
1. FastAPI API (uvicorn)
2. Dramatiq Worker (document ingestion actor)
3. Outbox Dispatcher (transactional outbox polling & AMQP publisher)
4. Reconciliation Worker (periodic cleaner for abandoned uploads)

Lifecycle & Fault Tolerance:
- Starts all four processes concurrently with line-buffered, tagged stdout/stderr.
- Signal handlers only record a request for shutdown; actual graceful shutdown
  is executed from the main supervisor loop.
- Monitors child processes: if ANY child process terminates unexpectedly (even with exit code 0),
  the supervisor marks the run as a failure, terminates remaining child processes,
  and exits with a non-zero exit code.
- Graceful shutdown timeout is configurable via SUPERVISOR_GRACEFUL_TIMEOUT (default 25s),
  ensuring it fits comfortably within Render's 30s SIGTERM termination window.
- Threads streaming process logs are cleanly joined with a short timeout.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

# Configuration
# Render gives 30 seconds between SIGTERM and SIGKILL.
# Default to 25s to leave a 5s safety margin.
GRACEFUL_TIMEOUT_SECONDS = int(os.environ.get("SUPERVISOR_GRACEFUL_TIMEOUT", "25"))

# Process definitions: (name, command_args)
PROCESS_DEFINITIONS = [
    {
        "name": "api",
        "cmd": [sys.executable, "run.py"],
    },
    {
        "name": "worker",
        # Use python -m dramatiq with 1 process and 2 threads to conserve memory on Render Free
        "cmd": [
            sys.executable,
            "-m",
            "dramatiq",
            "--processes",
            "1",
            "--threads",
            "2",
            "app.workers.document_tasks",
        ],
    },
    {
        "name": "dispatcher",
        "cmd": [sys.executable, "run_dispatcher.py"],
    },
    {
        "name": "reconciler",
        "cmd": [sys.executable, "run_reconciler.py"],
    },
]


class ProcessSupervisor:
    def __init__(
        self,
        process_defs: List[Dict[str, any]],
        graceful_timeout: int = GRACEFUL_TIMEOUT_SECONDS,
    ):
        self.process_defs = process_defs
        self.graceful_timeout = graceful_timeout
        self.processes: Dict[str, subprocess.Popen] = {}
        self.running = False
        self.shutdown_requested = False
        self.shutdown_signal_name: Optional[str] = None
        self.failed_process_name: Optional[str] = None
        self.failed_exit_code: Optional[int] = None
        self._threads: List[threading.Thread] = []

    def request_shutdown(self, signal_name: str):
        """Asynchronously records that a shutdown was requested by an OS signal."""
        self.shutdown_requested = True
        self.shutdown_signal_name = signal_name

    def _stream_output(self, name: str, pipe):
        """Streams child process stdout/stderr with a tagged prefix."""
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                text = line.rstrip()
                print(f"[{name}] {text}", flush=True)
        except Exception:
            pass
        finally:
            pipe.close()

    def start(self) -> bool:
        """
        Spawns all configured child processes.
        Returns True if ALL processes spawned successfully; otherwise terminates
        any already-spawned processes and returns False.
        """
        self.running = True
        print("[supervisor] Starting SourceMind backend services...", flush=True)

        for pdef in self.process_defs:
            name = pdef["name"]
            cmd = pdef["cmd"]
            print(f"[supervisor] Spawning '{name}': {' '.join(cmd)}", flush=True)

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
                self.processes[name] = proc

                # Start background log streaming threads
                t_out = threading.Thread(
                    target=self._stream_output, args=(name, proc.stdout), daemon=True
                )
                t_err = threading.Thread(
                    target=self._stream_output, args=(f"{name}:err", proc.stderr), daemon=True
                )
                t_out.start()
                t_err.start()
                self._threads.extend([t_out, t_err])

            except Exception as e:
                print(f"[supervisor] ERROR: Failed to spawn '{name}': {e}", file=sys.stderr, flush=True)
                self.failed_process_name = name
                self.failed_exit_code = 1
                self.shutdown(exit_code=1)
                return False

        print(f"[supervisor] All {len(self.processes)} processes spawned successfully.", flush=True)
        return True

    def monitor(self) -> int:
        """
        Monitors running child processes until shutdown is requested or a process exits.
        Returns:
            0 if clean shutdown requested via signal.
            Non-zero exit code if a child process exits unexpectedly or startup failed.
        """
        if not self.running:
            return self.failed_exit_code if self.failed_exit_code is not None else 1

        while self.running:
            # 1. Check if shutdown was requested by an OS signal handler
            if self.shutdown_requested:
                print(
                    f"\n[supervisor] Shutdown requested via {self.shutdown_signal_name or 'signal'}. Executing graceful shutdown...",
                    flush=True,
                )
                self.shutdown(exit_code=0)
                return 0

            # 2. Check health of all spawned child processes
            for name, proc in list(self.processes.items()):
                ret = proc.poll()
                if ret is not None:
                    # Child process exited! In this architecture, all 4 processes are long-running daemons.
                    # ANY termination (even exit code 0) is unexpected while the supervisor is active.
                    effective_code = ret if ret != 0 else 1
                    print(
                        f"[supervisor] CRITICAL: Child process '{name}' exited unexpectedly with code {ret} (effective failure code: {effective_code}).",
                        file=sys.stderr,
                        flush=True,
                    )
                    self.failed_process_name = name
                    self.failed_exit_code = effective_code
                    self.shutdown(exit_code=effective_code)
                    return effective_code

            time.sleep(0.5)

        return self.failed_exit_code if self.failed_exit_code is not None else 0

    def shutdown(self, exit_code: Optional[int] = None):
        """
        Gracefully terminates all child processes within self.graceful_timeout using a global deadline.
        If any processes do not stop before the deadline, broadcasts SIGKILL immediately to all surviving processes.
        """
        if self.shutdown_requested and not self.running:
            return
        self.shutdown_requested = True

        if exit_code is not None and self.failed_exit_code is None:
            self.failed_exit_code = exit_code

        print(
            f"[supervisor] Initiating shutdown of all processes (timeout: {self.graceful_timeout}s)...",
            flush=True,
        )

        # 1. Send SIGTERM to all active child processes
        for name, proc in self.processes.items():
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception as e:
                    print(f"[supervisor] Error sending SIGTERM to '{name}': {e}", flush=True)

        # 2. Wait for processes to exit within a unified monotonic deadline
        deadline = time.monotonic() + self.graceful_timeout
        while time.monotonic() < deadline:
            active_procs = [p for p in self.processes.values() if p.poll() is None]
            if not active_procs:
                break
            time.sleep(0.1)

        # 3. Handle any processes still running after the global deadline
        surviving = [(name, proc) for name, proc in self.processes.items() if proc.poll() is None]
        if surviving:
            print(
                f"[supervisor] Global shutdown deadline ({self.graceful_timeout}s) expired. "
                f"Sending SIGKILL to {len(surviving)} surviving processes: {[name for name, _ in surviving]}...",
                file=sys.stderr,
                flush=True,
            )
            # Send SIGKILL to all surviving processes first
            for name, proc in surviving:
                try:
                    proc.kill()
                except Exception as e:
                    print(f"[supervisor] Error sending SIGKILL to '{name}': {e}", file=sys.stderr, flush=True)

            # Wait briefly with a single short deadline (max 1.5s total) for OS to reap killed processes
            kill_deadline = time.monotonic() + 1.5
            while time.monotonic() < kill_deadline:
                if all(p.poll() is not None for _, p in surviving):
                    break
                time.sleep(0.1)

        # 4. Log individual final process statuses
        for name, proc in self.processes.items():
            ret = proc.poll()
            status_desc = f"exit code {ret}" if ret is not None else "still running"
            print(f"[supervisor] Process '{name}' stopped ({status_desc}).", flush=True)

        # 5. Join log streaming threads with a short timeout to prevent hanging
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=0.2)

        self.running = False
        print("[supervisor] Shutdown complete.", flush=True)


def main():
    supervisor = ProcessSupervisor(PROCESS_DEFINITIONS)

    def sig_handler(signum, frame):
        try:
            sig_name = signal.Signals(signum).name
        except Exception:
            sig_name = f"SIGNAL_{signum}"
        supervisor.request_shutdown(sig_name)

    # Register OS signal handlers
    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, sig_handler)

    started_ok = supervisor.start()
    if not started_ok:
        sys.exit(supervisor.failed_exit_code if supervisor.failed_exit_code is not None else 1)

    exit_code = supervisor.monitor()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
