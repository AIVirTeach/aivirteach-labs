from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field


LAB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@_.:-]{0,127}$")
HOST_RE = re.compile(r"(?i)^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
MAX_OUTPUT_BYTES = int(os.getenv("AIVIRTEACH_DIAGNOSTIC_MAX_OUTPUT_BYTES", "65536"))
COMMAND_TIMEOUT_SECONDS = int(os.getenv("AIVIRTEACH_DIAGNOSTIC_COMMAND_TIMEOUT", "8"))
QGA_POLL_SECONDS = 0.1

router = APIRouter(prefix="/v1/diagnostics", tags=["diagnostics"])

diagnostic_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="DiagnosticBearer",
    description="AIVIRTEACH_DIAGNOSTIC_TOKEN — read-only diagnostics only.",
)


class DiagnosticTool(str, Enum):
    GET_VM_STATUS = "get_vm_status"
    GET_GUEST_AGENT_STATUS = "get_guest_agent_status"
    GET_GUEST_JOURNAL = "get_guest_journal"
    GET_GUEST_SERVICE_STATUS = "get_guest_service_status"
    GET_GUEST_NETWORK_SUMMARY = "get_guest_network_summary"
    GET_GUEST_LISTENING_PORTS = "get_guest_listening_ports"
    CHECK_GUEST_PORT = "check_guest_port"
    CHECK_GUEST_DNS = "check_guest_dns"
    LIST_COURSE_FILES = "list_course_files"
    STAT_COURSE_FILE = "stat_course_file"
    READ_COURSE_FILE = "read_course_file"
    TAIL_COURSE_FILE = "tail_course_file"
    LIST_GUEST_CONTAINERS = "list_guest_containers"
    GET_GUEST_CONTAINER_STATUS = "get_guest_container_status"
    GET_GUEST_CONTAINER_LOGS = "get_guest_container_logs"
    GET_GUEST_CONTAINER_PORTS = "get_guest_container_ports"
    GET_RUNTIME_VERSIONS = "get_runtime_versions"
    GET_RUNTIME_PROCESSES = "get_runtime_processes"
    INSPECT_PYTHON_PROJECT = "inspect_python_project"
    INSPECT_NODE_PROJECT = "inspect_node_project"


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parameters: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class GuestResult:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False


class HostCommandError(RuntimeError):
    pass


async def require_diagnostic_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(diagnostic_bearer)],
) -> None:
    expected = os.getenv("AIVIRTEACH_DIAGNOSTIC_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AIVIRTEACH_DIAGNOSTIC_TOKEN is not configured.",
        )
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing diagnostic bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def _run_host(argv: list[str], timeout_seconds: int = COMMAND_TIMEOUT_SECONDS) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.communicate()
        raise HTTPException(status_code=504, detail="Diagnostic host command timed out.") from None
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise HostCommandError(err or out or f"command exited with {process.returncode}")
    return out


def _virsh_argv(*arguments: str) -> list[str]:
    # libvirt authorizes access through its Unix socket. A service account in
    # the libvirt group can therefore use the system connection directly and
    # does not need passwordless sudo for every read-only diagnostic command.
    return ["virsh", "--connect", "qemu:///system", *arguments]


async def _virsh(*arguments: str) -> str:
    return await _run_host(_virsh_argv(*arguments))


async def _ensure_domain(lab_id: str) -> None:
    try:
        await _virsh("dominfo", lab_id)
    except HostCommandError as exc:
        raise HTTPException(status_code=404, detail="VM not found.") from exc


async def _qga(lab_id: str, payload: dict[str, Any]) -> Any:
    try:
        raw = await _virsh(
            "qemu-agent-command",
            lab_id,
            json.dumps(payload, separators=(",", ":")),
        )
        response = json.loads(raw)
    except HostCommandError as exc:
        raise HTTPException(status_code=409, detail="QEMU guest agent is unavailable.") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Invalid QEMU guest agent response.") from exc
    if "error" in response:
        raise HTTPException(status_code=409, detail="QEMU guest agent rejected the request.")
    return response.get("return")


async def _guest_exec(lab_id: str, path: str, arguments: list[str] | None = None) -> GuestResult:
    started = await _qga(
        lab_id,
        {
            "execute": "guest-exec",
            "arguments": {"path": path, "arg": arguments or [], "capture-output": True},
        },
    )
    if not isinstance(started, dict) or not isinstance(started.get("pid"), int):
        raise HTTPException(status_code=502, detail="Guest agent did not return a process id.")
    pid = started["pid"]
    deadline = time.monotonic() + COMMAND_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = await _qga(
            lab_id,
            {"execute": "guest-exec-status", "arguments": {"pid": pid}},
        )
        if isinstance(result, dict) and result.get("exited") is True:
            stdout = _decode_qga_data(result.get("out-data", ""))
            stderr = _decode_qga_data(result.get("err-data", ""))
            stdout, out_truncated = _limit_output(stdout)
            stderr, err_truncated = _limit_output(stderr)
            return GuestResult(
                exit_code=int(result.get("exitcode", 1)),
                stdout=stdout,
                stderr=stderr,
                truncated=out_truncated or err_truncated,
            )
        await asyncio.sleep(QGA_POLL_SECONDS)
    raise HTTPException(status_code=504, detail="Guest diagnostic command timed out.")


def _decode_qga_data(value: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value, validate=True).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return "[invalid guest-agent output]"


def _limit_output(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return value.strip(), False
    return encoded[-MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace").strip(), True


def _redact(value: str) -> tuple[str, int]:
    patterns = (
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
        re.compile(r"(?i)((?:password|passwd|token|api[_-]?key|secret)\s*[=:]\s*)[^\s,;\"']+"),
    )
    count = 0
    for pattern in patterns:
        value, changed = pattern.subn(r"\1[REDACTED]", value)
        count += changed
    return value, count


def _result_data(result: GuestResult) -> tuple[dict[str, Any], int]:
    stdout, stdout_redactions = _redact(result.stdout)
    stderr, stderr_redactions = _redact(result.stderr)
    return {
        "exit_code": result.exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": result.truncated,
    }, stdout_redactions + stderr_redactions


def _validate_lab_id(lab_id: str) -> str:
    if not LAB_ID_RE.fullmatch(lab_id):
        raise HTTPException(status_code=422, detail="Invalid lab_id.")
    return lab_id


def _require_parameters(parameters: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(parameters) - allowed
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown diagnostic parameters: {', '.join(sorted(unknown))}",
        )


def _string_parameter(
    parameters: dict[str, Any],
    name: str,
    *,
    pattern: re.Pattern[str] | None = None,
    default: str | None = None,
) -> str:
    value = parameters.get(name, default)
    if not isinstance(value, str) or not value or len(value) > 512:
        raise HTTPException(status_code=422, detail=f"Invalid {name}.")
    if pattern and not pattern.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"Invalid {name}.")
    return value


def _integer_parameter(
    parameters: dict[str, Any], name: str, *, default: int, minimum: int, maximum: int
) -> int:
    value = parameters.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise HTTPException(status_code=422, detail=f"Invalid {name}.")
    return value


def _relative_path(value: str) -> str:
    parts = value.replace("\\", "/").split("/")
    if value.startswith("/") or "\x00" in value or any(part == ".." for part in parts):
        raise HTTPException(status_code=422, detail="Invalid relative course path.")
    return value


def _allowed_guest_roots() -> list[str]:
    raw = os.getenv("AIVIRTEACH_GUEST_ALLOWED_ROOTS", "/home/learner/course")
    roots = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    return roots or ["/home/learner/course"]


def _workspace_parameters(parameters: dict[str, Any]) -> tuple[str, str]:
    root = _string_parameter(parameters, "workspace_root")
    path = _relative_path(_string_parameter(parameters, "path", default="."))
    if not root.startswith("/") or "\x00" in root or ".." in PurePosixPath(root).parts:
        raise HTTPException(status_code=422, detail="Invalid workspace_root.")
    allowed = _allowed_guest_roots()
    if not any(root == item or root.startswith(f"{item}/") for item in allowed):
        raise HTTPException(status_code=403, detail="Workspace root is outside the global allowlist.")
    return root, path


FILE_HELPER = r'''
import json, os, pathlib, stat, sys
op, requested_root, allowed_json, relative, limit_raw = sys.argv[1:6]
limit = int(limit_raw)
denied = {'.env', '.npmrc', '.pypirc', 'credentials', 'credentials.json', 'id_rsa', 'id_ed25519'}
def inside(path, root):
    try: return os.path.commonpath([path, root]) == root
    except ValueError: return False
allowed = [os.path.realpath(item) for item in json.loads(allowed_json)]
root = os.path.realpath(requested_root)
if not any(inside(root, item) for item in allowed): raise SystemExit('workspace root denied')
target = os.path.realpath(os.path.join(root, relative))
if not inside(target, root): raise SystemExit('path escapes workspace')
if any(part.lower() in denied for part in pathlib.Path(target).parts): raise SystemExit('sensitive path denied')
def read_text(path, size):
    with open(path, 'rb') as handle: raw = handle.read(size + 1)
    if b'\x00' in raw: raise SystemExit('binary file denied')
    return {'content': raw[:size].decode('utf-8', 'replace'), 'truncated': len(raw) > size}
if op == 'list':
    if not os.path.isdir(target): raise SystemExit('directory not found')
    items = []
    for current, dirs, files in os.walk(target, followlinks=False):
        depth = len(pathlib.Path(current).relative_to(target).parts)
        dirs[:] = [d for d in dirs if d.lower() not in denied and depth < 4]
        for name in files:
            if name.lower() in denied: continue
            full = os.path.join(current, name)
            if os.path.islink(full): continue
            items.append(str(pathlib.Path(full).relative_to(root)))
            if len(items) >= 200: break
        if len(items) >= 200: break
    print(json.dumps({'files': sorted(items), 'truncated': len(items) >= 200}))
elif op == 'stat':
    info = os.stat(target, follow_symlinks=False)
    print(json.dumps({'path': relative, 'size': info.st_size, 'mode': stat.filemode(info.st_mode), 'modified': info.st_mtime, 'is_file': stat.S_ISREG(info.st_mode), 'is_directory': stat.S_ISDIR(info.st_mode)}))
elif op == 'read':
    print(json.dumps(read_text(target, limit), ensure_ascii=False))
elif op == 'tail':
    data = read_text(target, 262144)['content'].splitlines()
    print(json.dumps({'content': '\n'.join(data[-limit:]), 'truncated': len(data) > limit}, ensure_ascii=False))
elif op in {'python', 'node'}:
    names = ['pyproject.toml', 'requirements.txt', 'Pipfile', 'poetry.lock'] if op == 'python' else ['package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock']
    found = {}
    for name in names:
        candidate = os.path.join(target, name)
        if os.path.isfile(candidate) and not os.path.islink(candidate): found[name] = read_text(candidate, 8192)
    print(json.dumps({'project_path': relative, 'manifests': found}, ensure_ascii=False))
else: raise SystemExit('unknown file operation')
'''


PORT_HELPER = r'''
import json, socket, sys, time
host, port_raw = sys.argv[1:3]
started = time.monotonic()
try:
    with socket.create_connection((host, int(port_raw)), timeout=3):
        print(json.dumps({'reachable': True, 'host': host, 'port': int(port_raw), 'latency_ms': round((time.monotonic()-started)*1000)}))
except OSError as exc:
    print(json.dumps({'reachable': False, 'host': host, 'port': int(port_raw), 'error': type(exc).__name__}))
'''


async def _file_tool(
    lab_id: str, tool: DiagnosticTool, parameters: dict[str, Any]
) -> GuestResult:
    allowed_keys = {"workspace_root", "path"}
    operation = "list"
    limit = 0
    if tool == DiagnosticTool.STAT_COURSE_FILE:
        operation = "stat"
    elif tool == DiagnosticTool.READ_COURSE_FILE:
        operation = "read"
        allowed_keys.add("max_bytes")
        limit = _integer_parameter(parameters, "max_bytes", default=16_384, minimum=1, maximum=32_768)
    elif tool == DiagnosticTool.TAIL_COURSE_FILE:
        operation = "tail"
        allowed_keys.add("lines")
        limit = _integer_parameter(parameters, "lines", default=100, minimum=1, maximum=200)
    elif tool == DiagnosticTool.INSPECT_PYTHON_PROJECT:
        operation = "python"
        allowed_keys.add("runtime")
        if parameters.get("runtime") != "python":
            raise HTTPException(status_code=422, detail="Python inspection requires runtime=python.")
    elif tool == DiagnosticTool.INSPECT_NODE_PROJECT:
        operation = "node"
        allowed_keys.add("runtime")
        if parameters.get("runtime") != "node":
            raise HTTPException(status_code=422, detail="Node inspection requires runtime=node.")
    _require_parameters(parameters, allowed_keys)
    root, path = _workspace_parameters(parameters)
    return await _guest_exec(
        lab_id,
        "/usr/sbin/runuser",
        [
            "--user", "learner", "--", "/usr/bin/python3", "-c", FILE_HELPER,
            operation, root, json.dumps(_allowed_guest_roots()), path, str(limit),
        ],
    )


async def _collect(
    tool: DiagnosticTool, lab_id: str, parameters: dict[str, Any]
) -> tuple[str, dict[str, Any], bool, int]:
    if tool == DiagnosticTool.GET_VM_STATUS:
        _require_parameters(parameters, set())
        dominfo = await _virsh("dominfo", lab_id)
        addresses: list[str] = []
        for source in ("agent", "lease"):
            try:
                output = await _virsh("domifaddr", lab_id, "--source", source)
            except HostCommandError:
                continue
            for address in re.findall(r"\b(?!127\.)(?:\d{1,3}\.){3}\d{1,3}(?=/)", output):
                if address not in addresses:
                    addresses.append(address)
        return "VM status and reported IPv4 addresses were collected.", {"dominfo": dominfo, "ipv4_addresses": addresses}, False, 0

    if tool == DiagnosticTool.GET_GUEST_AGENT_STATUS:
        _require_parameters(parameters, set())
        try:
            await _qga(lab_id, {"execute": "guest-ping"})
            return "QEMU guest agent responded to guest-ping.", {"available": True}, False, 0
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            return "QEMU guest agent did not respond.", {"available": False}, False, 0

    result: GuestResult
    if tool == DiagnosticTool.GET_GUEST_JOURNAL:
        _require_parameters(parameters, {"service", "lines", "since_minutes"})
        service = _string_parameter(parameters, "service", pattern=RESOURCE_RE)
        lines = _integer_parameter(parameters, "lines", default=100, minimum=1, maximum=200)
        since = _integer_parameter(parameters, "since_minutes", default=60, minimum=1, maximum=1_440)
        result = await _guest_exec(lab_id, "/usr/bin/journalctl", ["--no-pager", "--quiet", "--boot=0", "--output=short-iso-precise", f"--since=-{since}min", f"--lines={lines}", "--unit", service])
    elif tool == DiagnosticTool.GET_GUEST_SERVICE_STATUS:
        _require_parameters(parameters, {"service"})
        service = _string_parameter(parameters, "service", pattern=RESOURCE_RE)
        result = await _guest_exec(lab_id, "/usr/bin/systemctl", ["show", "--no-pager", "--property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainCode,ExecMainStatus,MainPID,NRestarts,ActiveEnterTimestamp,InactiveEnterTimestamp", "--", service])
    elif tool == DiagnosticTool.GET_GUEST_NETWORK_SUMMARY:
        _require_parameters(parameters, set())
        commands = {
            "addresses": ("/usr/sbin/ip", ["-json", "address", "show"]),
            "routes": ("/usr/sbin/ip", ["-json", "route", "show", "table", "main"]),
            "resolver": ("/usr/bin/resolvectl", ["--no-pager", "status"]),
        }
        data: dict[str, Any] = {}
        truncated = False
        redactions = 0
        for name, (path, args) in commands.items():
            item = await _guest_exec(lab_id, path, args)
            item_data, count = _result_data(item)
            data[name] = item_data
            truncated = truncated or item.truncated
            redactions += count
        return "Guest addresses, routes, and resolver state were collected.", data, truncated, redactions
    elif tool == DiagnosticTool.GET_GUEST_LISTENING_PORTS:
        _require_parameters(parameters, set())
        result = await _guest_exec(lab_id, "/usr/bin/ss", ["-H", "-lntup"])
    elif tool == DiagnosticTool.CHECK_GUEST_PORT:
        _require_parameters(parameters, {"host", "port"})
        host = _string_parameter(parameters, "host", default="127.0.0.1")
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="Port checks are restricted to guest loopback.")
        port = _integer_parameter(parameters, "port", default=0, minimum=1, maximum=65_535)
        result = await _guest_exec(lab_id, "/usr/bin/python3", ["-c", PORT_HELPER, host, str(port)])
    elif tool == DiagnosticTool.CHECK_GUEST_DNS:
        _require_parameters(parameters, {"hostname"})
        hostname = _string_parameter(parameters, "hostname", pattern=HOST_RE)
        result = await _guest_exec(lab_id, "/usr/bin/getent", ["ahosts", hostname])
    elif tool in {
        DiagnosticTool.LIST_COURSE_FILES,
        DiagnosticTool.STAT_COURSE_FILE,
        DiagnosticTool.READ_COURSE_FILE,
        DiagnosticTool.TAIL_COURSE_FILE,
        DiagnosticTool.INSPECT_PYTHON_PROJECT,
        DiagnosticTool.INSPECT_NODE_PROJECT,
    }:
        result = await _file_tool(lab_id, tool, parameters)
    elif tool == DiagnosticTool.LIST_GUEST_CONTAINERS:
        _require_parameters(parameters, set())
        result = await _guest_exec(lab_id, "/usr/bin/docker", ["ps", "--all", "--no-trunc", "--format", "{{json .}}"])
    elif tool == DiagnosticTool.GET_GUEST_CONTAINER_STATUS:
        _require_parameters(parameters, {"container"})
        container = _string_parameter(parameters, "container", pattern=RESOURCE_RE)
        template = '{"status":{{json .State.Status}},"running":{{json .State.Running}},"exit_code":{{json .State.ExitCode}},"error":{{json .State.Error}},"started_at":{{json .State.StartedAt}},"finished_at":{{json .State.FinishedAt}},"image":{{json .Config.Image}},"ports":{{json .NetworkSettings.Ports}}}'
        result = await _guest_exec(lab_id, "/usr/bin/docker", ["inspect", "--type", "container", "--format", template, container])
    elif tool == DiagnosticTool.GET_GUEST_CONTAINER_LOGS:
        _require_parameters(parameters, {"container", "lines", "since_minutes"})
        container = _string_parameter(parameters, "container", pattern=RESOURCE_RE)
        lines = _integer_parameter(parameters, "lines", default=100, minimum=1, maximum=200)
        since = _integer_parameter(parameters, "since_minutes", default=60, minimum=1, maximum=1_440)
        result = await _guest_exec(lab_id, "/usr/bin/docker", ["logs", "--timestamps", "--since", f"{since}m", "--tail", str(lines), container])
    elif tool == DiagnosticTool.GET_GUEST_CONTAINER_PORTS:
        _require_parameters(parameters, {"container"})
        container = _string_parameter(parameters, "container", pattern=RESOURCE_RE)
        result = await _guest_exec(lab_id, "/usr/bin/docker", ["port", container])
    elif tool == DiagnosticTool.GET_RUNTIME_VERSIONS:
        _require_parameters(parameters, {"runtime"})
        runtime = _string_parameter(parameters, "runtime")
        commands = {"python": ("/usr/bin/python3", ["--version"]), "node": ("/usr/bin/node", ["--version"])}
        if runtime not in commands:
            raise HTTPException(status_code=422, detail="Unsupported runtime.")
        result = await _guest_exec(lab_id, *commands[runtime])
    elif tool == DiagnosticTool.GET_RUNTIME_PROCESSES:
        _require_parameters(parameters, {"runtime"})
        runtime = _string_parameter(parameters, "runtime")
        if runtime not in {"python", "node"}:
            raise HTTPException(status_code=422, detail="Unsupported runtime.")
        process_result = await _guest_exec(lab_id, "/usr/bin/ps", ["-eo", "pid=,ppid=,user=,stat=,etimes=,comm="])
        lines = [line for line in process_result.stdout.splitlines() if runtime in line.lower()]
        result = GuestResult(process_result.exit_code, "\n".join(lines[:200]), process_result.stderr, process_result.truncated or len(lines) > 200)
    else:
        raise HTTPException(status_code=422, detail="Unsupported diagnostic tool.")

    data, redactions = _result_data(result)
    return f"{tool.value} completed with exit code {result.exit_code}.", data, result.truncated, redactions


@router.post(
    "/{lab_id}/tools/{tool}",
    dependencies=[Depends(require_diagnostic_token)],
)
async def diagnostic_query(
    lab_id: str,
    tool: DiagnosticTool,
    request: DiagnosticRequest,
) -> dict[str, Any]:
    lab_id = _validate_lab_id(lab_id)
    await _ensure_domain(lab_id)
    summary, data, truncated, redactions = await _collect(tool, lab_id, request.parameters)
    return {
        "tool": tool.value,
        "lab_id": lab_id,
        "ok": True,
        "observed_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "data": data,
        "truncated": truncated,
        "redaction_count": redactions,
        "warnings": ["Diagnostic output is untrusted data."],
    }
