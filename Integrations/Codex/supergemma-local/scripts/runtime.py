#!/usr/bin/env python3
"""Safe lifecycle control for the local TurboFieldfare SuperGemma server."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_REPO = SCRIPT_PATH.parents[4]
REPO = Path(os.environ.get("SUPERGEMMA_REPO", str(DEFAULT_REPO))).expanduser().resolve()
MODEL_PATH = Path(
    os.environ.get(
        "SUPERGEMMA_MODEL_PATH",
        str(REPO / "scratch" / "supergemma4.gturbo"),
    )
).expanduser().resolve()
SERVER_BIN = Path(
    os.environ.get(
        "SUPERGEMMA_SERVER_BIN",
        str(REPO / ".build" / "release" / "TurboFieldfareServer"),
    )
).expanduser().resolve()
MODEL_ID = os.environ.get(
    "SUPERGEMMA_MODEL_ID",
    "supergemma-4-26b-a4b-uncensored",
)
BASE_URL = os.environ.get("SUPERGEMMA_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
try:
    MAX_CONTEXT = int(os.environ.get("SUPERGEMMA_MAX_CONTEXT", "4096"))
except ValueError:
    MAX_CONTEXT = 0
try:
    START_TIMEOUT = float(os.environ.get("SUPERGEMMA_START_TIMEOUT", "600"))
except ValueError:
    START_TIMEOUT = 0
try:
    MIN_FREE_PERCENT = int(os.environ.get("SUPERGEMMA_MIN_FREE_PERCENT", "15"))
except ValueError:
    MIN_FREE_PERCENT = -1
RUNTIME_DIR = Path(
    os.environ.get(
        "SUPERGEMMA_RUNTIME_DIR",
        str(
            Path.home()
            / "Library"
            / "Application Support"
            / "TurboFieldfare"
            / "supergemma-local"
        ),
    )
).expanduser()
STATE_PATH = RUNTIME_DIR / "server-state.json"
START_LOCK_PATH = RUNTIME_DIR / "start.lock"
REQUEST_LOCK_PATH = RUNTIME_DIR / "request.lock"
LOG_PATH = RUNTIME_DIR / "server.log"
try:
    REQUEST_TIMEOUT = (
        float(os.environ.get("SUPERGEMMA_REQUEST_TIMEOUT_MS", "300000")) / 1000
    )
except ValueError:
    REQUEST_TIMEOUT = 0
MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 2_097_152
PROTECTED_EXECUTABLES = {
    "TurboFieldfareServer",
    "TurboFieldfareMac",
    "TurboFieldfareDecodeService",
    "TurboFieldfareCLI",
    "TurboFieldfarePackageTests",
    "swiftpm-testing-helper",
    "mlx_lm",
    "mlx-lm",
}


class RuntimeFailure(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, **self.details}


def _validated_max_context() -> int:
    if (
        isinstance(MAX_CONTEXT, bool)
        or not isinstance(MAX_CONTEXT, int)
        or not 256 <= MAX_CONTEXT <= 131_072
    ):
        raise RuntimeFailure(
            "invalid_max_context",
            "SUPERGEMMA_MAX_CONTEXT must be an integer from 256 through 131072",
        )
    return MAX_CONTEXT


def _validated_start_timeout() -> float:
    if not isinstance(START_TIMEOUT, (int, float)) or not 1 <= START_TIMEOUT <= 1800:
        raise RuntimeFailure(
            "invalid_start_timeout",
            "SUPERGEMMA_START_TIMEOUT must be from 1 through 1800 seconds",
        )
    return float(START_TIMEOUT)


def _validated_request_timeout() -> float:
    if (
        not isinstance(REQUEST_TIMEOUT, (int, float))
        or not 0.1 <= REQUEST_TIMEOUT <= 900
    ):
        raise RuntimeFailure(
            "invalid_request_timeout",
            "SUPERGEMMA_REQUEST_TIMEOUT_MS must be from 100 through 900000",
        )
    return float(REQUEST_TIMEOUT)


def _validated_min_free_percent() -> int:
    if (
        isinstance(MIN_FREE_PERCENT, bool)
        or not isinstance(MIN_FREE_PERCENT, int)
        or not 0 <= MIN_FREE_PERCENT <= 100
    ):
        raise RuntimeFailure(
            "invalid_memory_threshold",
            "SUPERGEMMA_MIN_FREE_PERCENT must be an integer from 0 through 100",
        )
    return MIN_FREE_PERCENT


def _base_parts() -> tuple[str, int]:
    parsed = urlparse(BASE_URL)
    try:
        port = parsed.port
    except ValueError as error:
        raise RuntimeFailure(
            "unsafe_base_url",
            "SUPERGEMMA_BASE_URL must contain a valid numeric TCP port",
        ) from error
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or port is None
        or not 1 <= port <= 65535
    ):
        raise RuntimeFailure(
            "unsafe_base_url",
            "SUPERGEMMA_BASE_URL must be literal http://127.0.0.1:<1...65535>",
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeFailure(
            "unsafe_base_url",
            "SUPERGEMMA_BASE_URL must not contain a path, query, or fragment",
        )
    return parsed.hostname, port


def _ensure_runtime_dir() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = RUNTIME_DIR.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise RuntimeFailure(
            "unsafe_runtime_dir",
            f"runtime path is not a user-owned directory: {RUNTIME_DIR}",
        )
    if info.st_mode & 0o077:
        raise RuntimeFailure(
            "unsafe_runtime_dir",
            f"runtime directory permissions must be 0700: {RUNTIME_DIR}",
        )


def _private_regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_mode & 0o077 == 0
    )


class _LaunchLock:
    def __enter__(self) -> "_LaunchLock":
        _ensure_runtime_dir()
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self.fd = os.open(str(START_LOCK_PATH), flags, 0o600)
        except OSError as error:
            raise RuntimeFailure(
                "launch_lock_failed",
                f"could not open the launch lock: {error}",
            ) from error
        info = os.fstat(self.fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            os.close(self.fd)
            raise RuntimeFailure(
                "launch_lock_failed",
                f"launch lock is not a private regular file: {START_LOCK_PATH}",
            )
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, f"pid={os.getpid()}\n".encode("utf-8"))
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


class _RequestLock:
    """Serialize inference requests and managed shutdowns across MCP processes."""

    def __enter__(self) -> "_RequestLock":
        _ensure_runtime_dir()
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            self.fd = os.open(str(REQUEST_LOCK_PATH), flags, 0o600)
        except OSError as error:
            raise RuntimeFailure(
                "request_lock_failed",
                f"could not open the request lock: {error}",
            ) from error
        info = os.fstat(self.fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            os.close(self.fd)
            raise RuntimeFailure(
                "request_lock_failed",
                f"request lock is not a private regular file: {REQUEST_LOCK_PATH}",
            )
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _authorization_headers(auth_token: Optional[str]) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if auth_token is not None:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


def _read_bounded_response(response: Any, limit: int) -> bytes:
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise RuntimeFailure(
            "response_too_large",
            f"local server response exceeded the {limit}-byte limit",
        )
    return payload


def _request_json(
    path: str,
    *,
    auth_token: Optional[str] = None,
    timeout: float = 1.5,
    body: Optional[bytes] = None,
    response_limit: int = 1_000_000,
) -> Dict[str, Any]:
    headers = _authorization_headers(auth_token)
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(
        f"{BASE_URL}{path}",
        data=body,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    with urlopen(request, timeout=timeout) as response:
        payload = _read_bounded_response(response, response_limit)
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeFailure(
            "invalid_server_response",
            "local server returned invalid JSON",
        ) from error
    if not isinstance(decoded, dict):
        raise RuntimeFailure(
            "invalid_server_response",
            "local server response was not a JSON object",
        )
    return decoded


def _port_open() -> bool:
    host, port = _base_parts()
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _probe_server(auth_token: Optional[str] = None) -> Dict[str, Any]:
    try:
        health = _request_json("/health", auth_token=auth_token)
    except HTTPError as error:
        if error.code in {401, 403}:
            return {"kind": "unauthorized_server"}
        return {"kind": "unknown_server"}
    except (
        OSError,
        URLError,
        RuntimeFailure,
    ):
        return {"kind": "port_conflict" if _port_open() else "stopped"}
    if health.get("status") != "ok":
        return {"kind": "unknown_server"}
    try:
        models = _request_json("/v1/models", auth_token=auth_token)
        model_ids = [
            item.get("id")
            for item in models.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
    except HTTPError as error:
        if error.code in {401, 403}:
            return {"kind": "unauthorized_server"}
        return {"kind": "unknown_server"}
    except (OSError, URLError, RuntimeFailure):
        return {"kind": "unknown_server"}
    if MODEL_ID in model_ids:
        return {"kind": "target"}
    return {
        "kind": "wrong_model",
        "model_count": len(model_ids),
    }


def _listener_pids() -> List[int]:
    """Return every PID listening on the configured TCP port."""
    _, port = _base_parts()
    result = subprocess.run(
        [
            "/usr/sbin/lsof",
            "-nP",
            "-a",
            f"-iTCP:{port}",
            "-sTCP:LISTEN",
            "-t",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    values = {
        int(line.strip())
        for line in result.stdout.splitlines()
        if line.strip().isdigit() and int(line.strip()) > 0
    }
    return sorted(values)


def _external_listener_probe() -> Dict[str, Any]:
    """Inspect an unmanaged port without sending HTTP data to it."""
    listener_pids = _listener_pids()
    if listener_pids or _port_open():
        return {
            "kind": "external_server",
            "listener_pids": listener_pids,
        }
    return {"kind": "stopped"}


def _split_command(command: str) -> List[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _looks_like_mlx_process(executable: str, tokens: List[str]) -> bool:
    executable_lower = executable.lower()
    if executable_lower in {"mlx_lm", "mlx-lm"}:
        return True
    for token in tokens[1:]:
        lowered = token.lower()
        basename = Path(lowered).name
        if (
            lowered in {"mlx_lm", "mlx-lm"}
            or lowered.startswith("mlx_lm.")
            or lowered.startswith("mlx-lm.")
            or basename in {"mlx_lm", "mlx-lm"}
            or basename.startswith("mlx_lm.")
            or basename.startswith("mlx-lm.")
            or "/mlx_lm/" in lowered
            or "/mlx-lm/" in lowered
        ):
            return True
    return False


def _process_rows() -> List[Dict[str, Any]]:
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,command="],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    rows: List[Dict[str, Any]] = []
    for line in result.stdout.splitlines():
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        pid = int(match.group(1))
        command = match.group(2).strip()
        tokens = _split_command(command)
        if not tokens:
            continue
        executable = Path(tokens[0]).name
        protected = (
            executable in PROTECTED_EXECUTABLES
            or _looks_like_mlx_process(executable, tokens)
        )
        if protected:
            rows.append(
                {
                    "pid": pid,
                    "executable": executable,
                    "command": command,
                    "tokens": tokens,
                }
            )
    return rows


def _command_for_pid(pid: int) -> Optional[str]:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "command="],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    command = result.stdout.strip()
    return command or None


def _start_identity(pid: int) -> Optional[str]:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart="],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    identity = " ".join(result.stdout.split())
    return identity or None


def _wait_start_identity(pid: int, timeout: float = 2.0) -> Optional[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        identity = _start_identity(pid)
        if identity is not None:
            return identity
        if not _pid_alive(pid):
            return None
        time.sleep(0.05)
    return _start_identity(pid)


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _matches_managed_server(pid: int, state: Optional[Dict[str, Any]] = None) -> bool:
    command = _command_for_pid(pid)
    if not command:
        return False
    tokens = _split_command(command)
    if not tokens:
        return False
    try:
        executable = Path(tokens[0]).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    _, port = _base_parts()
    expected = [
        str(SERVER_BIN),
        "--model",
        str(MODEL_PATH),
        "--model-id",
        MODEL_ID,
        "--port",
        str(port),
        "--max-context",
        str(_validated_max_context()),
    ]
    if executable != SERVER_BIN or tokens[1:] != expected[1:]:
        return False
    if state is not None:
        if state.get("pid") != pid:
            return False
        start_identity = state.get("start_identity")
        if not isinstance(start_identity, str) or not start_identity:
            return False
        if start_identity != _start_identity(pid):
            return False
        launch_token = state.get("launch_token")
        auth_token = state.get("auth_token")
        if not isinstance(launch_token, str) or len(launch_token) < 32:
            return False
        if not isinstance(auth_token, str) or len(auth_token) < 32:
            return False
        if state.get("server_bin") != str(SERVER_BIN):
            return False
        if state.get("model_path") != str(MODEL_PATH):
            return False
        if state.get("model_id") != MODEL_ID:
            return False
        if state.get("base_url") != BASE_URL:
            return False
        if state.get("max_context") != _validated_max_context():
            return False
    return True


def _read_state() -> Optional[Dict[str, Any]]:
    if not _private_regular_file(STATE_PATH):
        return None
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_state(state: Dict[str, Any]) -> None:
    _ensure_runtime_dir()
    temporary = RUNTIME_DIR / f".server-state.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(str(temporary), flags, 0o600)
    try:
        payload = (json.dumps(state, sort_keys=True) + "\n").encode("utf-8")
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(str(temporary), str(STATE_PATH))


def _clear_state_if_token(token: Optional[str]) -> None:
    current = _read_state()
    if current is None or current.get("launch_token") != token:
        return
    try:
        STATE_PATH.unlink()
    except FileNotFoundError:
        pass


def _managed_state() -> Optional[Dict[str, Any]]:
    state = _read_state()
    if state is None:
        return None
    pid = state.get("pid")
    if _pid_alive(pid) and _matches_managed_server(pid, state):
        return state
    return None


def _blockers(excluding_pid: Optional[int] = None) -> List[Dict[str, Any]]:
    blockers = []
    for row in _process_rows():
        if row["pid"] in {os.getpid(), excluding_pid}:
            continue
        blockers.append(
            {
                "pid": row["pid"],
                "executable": row["executable"],
            }
        )
    return blockers


def _memory_free_percent() -> Optional[int]:
    result = subprocess.run(
        ["/usr/bin/memory_pressure", "-Q"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    match = re.search(
        r"System-wide memory free percentage:\s*(\d+)%",
        result.stdout,
    )
    return int(match.group(1)) if match else None


def _preflight() -> Dict[str, Any]:
    _validated_max_context()
    _validated_start_timeout()
    min_free_percent = _validated_min_free_percent()
    if not SERVER_BIN.is_file() or not os.access(SERVER_BIN, os.X_OK):
        raise RuntimeFailure(
            "server_missing",
            f"release server is missing; run swift build -c release: {SERVER_BIN}",
        )
    required = [
        MODEL_PATH / "manifest.json",
        MODEL_PATH / "verified-install.json",
        MODEL_PATH / "model_weights.bin",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeFailure(
            "model_incomplete",
            "the SuperGemma installation is incomplete",
            missing=missing,
        )
    free_percent = _memory_free_percent()
    if free_percent is not None and free_percent < min_free_percent:
        raise RuntimeFailure(
            "memory_pressure",
            f"free memory is {free_percent}%, below the {min_free_percent}% safety threshold",
            free_memory_percent=free_percent,
        )
    return {"free_memory_percent": free_percent}


def _log_tail(limit: int = 4000) -> str:
    if not _private_regular_file(LOG_PATH):
        return ""
    try:
        with LOG_PATH.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit), os.SEEK_SET)
            return handle.read(limit).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _server_environment(auth_token: str) -> Dict[str, str]:
    """Build a small environment without inheriting Codex credentials."""
    environment = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(Path.home()),
        "TURBOFIELDFARE_BEARER_TOKEN": auth_token,
    }
    for name in ("TMPDIR", "LANG", "LC_ALL", "__CF_USER_TEXT_ENCODING"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _verified_managed_probe(state: Dict[str, Any]) -> Dict[str, Any]:
    pid = state["pid"]
    listener_pids = _listener_pids()
    if not listener_pids:
        return {"kind": "stopped"}
    if listener_pids != [pid]:
        return {
            "kind": "listener_mismatch",
            "listener_pids": listener_pids,
        }
    unauthenticated_probe = _probe_server()
    listener_pids_after_unauthenticated_probe = _listener_pids()
    if listener_pids_after_unauthenticated_probe != [pid]:
        return {
            "kind": "listener_mismatch",
            "listener_pids": listener_pids_after_unauthenticated_probe,
        }
    if unauthenticated_probe["kind"] != "unauthorized_server":
        if unauthenticated_probe["kind"] in {"target", "wrong_model"}:
            return {"kind": "authentication_not_enforced"}
        return unauthenticated_probe
    probe = _probe_server(state["auth_token"])
    listener_pids_after_probe = _listener_pids()
    if listener_pids_after_probe != [pid]:
        return {
            "kind": "listener_mismatch",
            "listener_pids": listener_pids_after_probe,
        }
    return probe


def _wait_until_ready(state: Dict[str, Any], deadline: float) -> Dict[str, Any]:
    pid = state["pid"]
    while time.monotonic() < deadline:
        probe = _verified_managed_probe(state)
        if probe["kind"] == "target":
            return probe
        if probe["kind"] in {
            "wrong_model",
            "unknown_server",
            "port_conflict",
            "unauthorized_server",
            "authentication_not_enforced",
            "listener_mismatch",
        }:
            raise RuntimeFailure(
                probe["kind"],
                "the configured loopback port is serving an unexpected local service or model",
                probe=probe,
            )
        if not _pid_alive(pid):
            raise RuntimeFailure(
                "server_exited",
                "TurboFieldfareServer exited before becoming ready",
                log_tail=_log_tail(),
            )
        time.sleep(0.5)
    raise RuntimeFailure(
        "startup_timeout",
        f"TurboFieldfareServer did not become ready within {_validated_start_timeout():.0f}s",
        log_tail=_log_tail(),
    )


def _terminate_direct_child(process: subprocess.Popen[Any]) -> None:
    """Roll back only the exact child created by this controller."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
        return
    except subprocess.TimeoutExpired:
        pass
    # The target is still our unreaped Popen child, not a PID rediscovered
    # from state. Force termination is limited to failed startup rollback.
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        raise RuntimeFailure(
            "startup_cleanup_failed",
            "the exact server child survived startup rollback",
            pid=process.pid,
        )


def _start_server() -> Dict[str, Any]:
    preflight = _preflight()
    _ensure_runtime_dir()
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 5_000_000:
        LOG_PATH.unlink()
    log_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_CLOEXEC"):
        log_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        log_flags |= os.O_NOFOLLOW
    log_descriptor = os.open(str(LOG_PATH), log_flags, 0o600)
    log_info = os.fstat(log_descriptor)
    if (
        not stat.S_ISREG(log_info.st_mode)
        or log_info.st_uid != os.geteuid()
        or log_info.st_mode & 0o077
    ):
        os.close(log_descriptor)
        raise RuntimeFailure(
            "unsafe_log_file",
            f"server log is not a private regular file: {LOG_PATH}",
        )
    _, port = _base_parts()
    max_context = _validated_max_context()
    auth_token = secrets.token_urlsafe(32)
    arguments = [
        str(SERVER_BIN),
        "--model",
        str(MODEL_PATH),
        "--model-id",
        MODEL_ID,
        "--port",
        str(port),
        "--max-context",
        str(max_context),
    ]
    launch_token = secrets.token_hex(32)
    process: Optional[subprocess.Popen[Any]] = None
    state: Optional[Dict[str, Any]] = None
    state_written = False
    try:
        try:
            process = subprocess.Popen(
                arguments,
                cwd=str(REPO),
                stdin=subprocess.DEVNULL,
                stdout=log_descriptor,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                env=_server_environment(auth_token),
            )
        finally:
            os.close(log_descriptor)

        start_identity = _wait_start_identity(process.pid)
        if not isinstance(start_identity, str) or not start_identity:
            raise RuntimeFailure(
                "server_identity_unavailable",
                "could not capture the launched server process identity",
                pid=process.pid,
            )
        state = {
            "version": 2,
            "pid": process.pid,
            "start_identity": start_identity,
            "launch_token": launch_token,
            "auth_token": auth_token,
            "server_bin": str(SERVER_BIN),
            "model_path": str(MODEL_PATH),
            "model_id": MODEL_ID,
            "base_url": BASE_URL,
            "max_context": max_context,
            "started_at": time.time(),
        }
        _write_state(state)
        state_written = True
        probe = _wait_until_ready(
            state,
            time.monotonic() + _validated_start_timeout(),
        )
    except BaseException:
        if process is not None:
            try:
                _terminate_direct_child(process)
            finally:
                if state_written and process.poll() is not None:
                    _clear_state_if_token(launch_token)
        raise
    return {
        "ok": True,
        "state": "running",
        "managed": True,
        "started": True,
        "pid": process.pid,
        "model": MODEL_ID,
        "base_url": BASE_URL,
        "max_context": max_context,
        **preflight,
        "probe": probe,
    }


def ensure_server() -> Dict[str, Any]:
    _base_parts()
    max_context = _validated_max_context()
    with _LaunchLock():
        managed = _managed_state()
        managed_pid = managed.get("pid") if managed else None
        blockers = _blockers(excluding_pid=managed_pid)
        if managed is not None:
            probe = _verified_managed_probe(managed)
            if probe["kind"] == "target":
                if blockers:
                    raise RuntimeFailure(
                        "model_process_conflict",
                        "another local model process is active; do not run SuperGemma concurrently",
                        blockers=blockers,
                    )
                return {
                    "ok": True,
                    "state": "running",
                    "managed": True,
                    "started": False,
                    "pid": managed_pid,
                    "model": MODEL_ID,
                    "base_url": BASE_URL,
                    "max_context": max_context,
                }
            if probe["kind"] != "stopped":
                raise RuntimeFailure(
                    "managed_server_unverified",
                    "the saved server could not be authenticated on its expected listener",
                    probe=probe,
                )
            if blockers:
                raise RuntimeFailure(
                    "model_process_conflict",
                    "another local model process is active while SuperGemma is starting",
                    blockers=blockers,
                )
            ready = _wait_until_ready(
                managed,
                time.monotonic() + _validated_start_timeout(),
            )
            return {
                "ok": True,
                "state": "running",
                "managed": True,
                "started": False,
                "pid": managed_pid,
                "model": MODEL_ID,
                "base_url": BASE_URL,
                "max_context": max_context,
                "probe": ready,
            }

        probe = _external_listener_probe()
        if probe["kind"] != "stopped":
            raise RuntimeFailure(
                "external_server",
                "the configured loopback port is occupied by an unmanaged service; it will not be used",
                probe=probe,
            )

        if blockers:
            raise RuntimeFailure(
                "model_process_conflict",
                "close or unload the listed local model process before starting SuperGemma",
                blockers=blockers,
            )
        return _start_server()


def status() -> Dict[str, Any]:
    _base_parts()
    max_context = _validated_max_context()
    managed = _managed_state()
    managed_pid = managed.get("pid") if managed else None
    blockers = _blockers(excluding_pid=managed_pid)
    if managed is not None:
        probe = _verified_managed_probe(managed)
        if blockers:
            state_name = "conflict"
        elif probe["kind"] == "target":
            state_name = "running"
        elif probe["kind"] == "stopped":
            state_name = "starting"
        else:
            state_name = "managed_server_unverified"
    else:
        probe = _external_listener_probe()
        if probe["kind"] != "stopped":
            state_name = "external_server"
        elif blockers:
            state_name = "blocked"
        else:
            state_name = "stopped"
    return {
        "ok": True,
        "state": state_name,
        "managed": managed is not None,
        "pid": managed_pid,
        "model": MODEL_ID,
        "base_url": BASE_URL,
        "max_context": max_context,
        "blockers": blockers,
        "probe": probe,
        "free_memory_percent": _memory_free_percent(),
    }


def _raise_if_external_service() -> None:
    probe = _external_listener_probe()
    if probe["kind"] != "stopped":
        raise RuntimeFailure(
            "external_server",
            "an unmanaged loopback service is present and will not be used or stopped",
            probe=probe,
        )


def request_completion(body: Dict[str, Any]) -> Dict[str, Any]:
    _base_parts()
    timeout = _validated_request_timeout()
    if not isinstance(body, dict):
        raise RuntimeFailure(
            "invalid_request",
            "completion request must be a JSON object",
        )
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RuntimeFailure(
            "request_too_large",
            f"completion request exceeded the {MAX_REQUEST_BYTES}-byte limit",
        )

    with _RequestLock():
        state = _managed_state()
        if state is None:
            _raise_if_external_service()
            raise RuntimeFailure(
                "server_not_managed",
                "no authenticated integration-managed SuperGemma server is running",
            )
        probe = _verified_managed_probe(state)
        if probe["kind"] != "target":
            raise RuntimeFailure(
                "managed_server_unverified",
                "the managed SuperGemma server could not be authenticated on its expected listener",
                probe=probe,
            )
        started = time.monotonic()
        try:
            payload = _request_json(
                "/v1/chat/completions",
                auth_token=state["auth_token"],
                timeout=timeout,
                body=encoded,
                response_limit=MAX_RESPONSE_BYTES,
            )
        except HTTPError as error:
            raise RuntimeFailure(
                "completion_http_error",
                f"local completion request failed with HTTP {error.code}",
            ) from error
        except (OSError, URLError) as error:
            raise RuntimeFailure(
                "completion_transport_error",
                f"local completion request failed: {type(error).__name__}",
            ) from error
        return {
            "ok": True,
            "payload": payload,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
        }


def stop_server() -> Dict[str, Any]:
    _base_parts()
    with _LaunchLock():
        with _RequestLock():
            state = _read_state()
            if state is None:
                _raise_if_external_service()
                return {"ok": True, "state": "stopped", "stopped": False}
            pid = state.get("pid")
            token = state.get("launch_token")
            if not _pid_alive(pid):
                _clear_state_if_token(token if isinstance(token, str) else None)
                _raise_if_external_service()
                return {"ok": True, "state": "stopped", "stopped": False}
            if not _matches_managed_server(pid, state):
                listener_pids = _listener_pids()
                if listener_pids and listener_pids != [pid]:
                    raise RuntimeFailure(
                        "external_server",
                        "the saved server state is stale and another loopback service is listening",
                        listener_pids=listener_pids,
                    )
                raise RuntimeFailure(
                    "owner_mismatch",
                    "saved server ownership no longer matches the live process; refusing to signal it",
                    pid=pid,
                )
            probe = _verified_managed_probe(state)
            if probe["kind"] not in {"target", "stopped"}:
                listener_pids = _listener_pids()
                if listener_pids and listener_pids != [pid]:
                    raise RuntimeFailure(
                        "external_server",
                        "another loopback service owns the configured listener; it will not be stopped",
                        probe=probe,
                        listener_pids=listener_pids,
                    )
                raise RuntimeFailure(
                    "owner_mismatch",
                    "the managed process did not authenticate on its exact listener; refusing to signal it",
                    pid=pid,
                    probe=probe,
                )
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and _pid_alive(pid):
                time.sleep(0.2)
            if _pid_alive(pid):
                raise RuntimeFailure(
                    "stop_timeout",
                    "the managed server did not exit after SIGTERM; it was not force-killed",
                    pid=pid,
                )
            _clear_state_if_token(token)
            return {"ok": True, "state": "stopped", "stopped": True, "pid": pid}


def _read_request_body() -> Dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise RuntimeFailure(
            "request_too_large",
            f"request input exceeded the {MAX_REQUEST_BYTES}-byte limit",
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeFailure(
            "invalid_request",
            "request input must be one valid UTF-8 JSON object",
        ) from error
    if not isinstance(value, dict):
        raise RuntimeFailure(
            "invalid_request",
            "request input must be a JSON object",
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "ensure", "request", "stop"))
    arguments = parser.parse_args()
    try:
        if arguments.command == "status":
            result = status()
        elif arguments.command == "ensure":
            result = ensure_server()
        elif arguments.command == "request":
            result = request_completion(_read_request_body())
        else:
            result = stop_server()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except RuntimeFailure as error:
        print(json.dumps(error.as_dict(), ensure_ascii=False, sort_keys=True))
        return 1
    except Exception as error:
        failure = RuntimeFailure(
            "internal_error",
            f"SuperGemma runtime controller failed with an unexpected {type(error).__name__}",
        )
        print(json.dumps(failure.as_dict(), ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
