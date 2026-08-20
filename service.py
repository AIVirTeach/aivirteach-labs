from __future__ import annotations

import asyncio
import hmac
import os
import re
import signal
from collections.abc import Sequence
from enum import Enum
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field


PROJECT_DIR = Path(__file__).resolve().parent
LIBVIRT_DIR = Path(
    os.getenv("AIVIRTEACH_LIBVIRT_DIR", str(PROJECT_DIR / "libvirt"))
).resolve()
VM_CONTROL_SCRIPT = LIBVIRT_DIR / "scripts" / "vm-control.sh"
CREATE_VM_SCRIPT = LIBVIRT_DIR / "scripts" / "create-learner-vm.sh"
LAB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

COMMAND_TIMEOUT_SECONDS = int(os.getenv("AIVIRTEACH_COMMAND_TIMEOUT", "30"))
CREATE_TIMEOUT_SECONDS = int(os.getenv("AIVIRTEACH_CREATE_TIMEOUT", "180"))

app = FastAPI(
    title="AIVirTeach VM Manager",
    version="1.0.0",
    description="A restricted HTTP interface for the AIVirTeach libvirt scripts.",
)

_lab_locks: dict[str, asyncio.Lock] = {}

admin_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AdminBearer",
    description="AIVIRTEACH_API_TOKEN — VM lifecycle and credentials.",
)


class VMAction(str, Enum):
    START = "start"
    STOP = "stop"
    FORCE_STOP = "force-stop"
    REBOOT = "reboot"


class CreateVMRequest(BaseModel):
    lab_id: str = Field(min_length=1, max_length=63)
    memory_mb: int = Field(default=4096, ge=1024, le=32768)
    vcpus: int = Field(default=2, ge=1, le=16)
    autostart: bool = False


class OperationResponse(BaseModel):
    lab_id: str
    operation: str
    message: str


def _validate_lab_id(lab_id: str) -> str:
    if not LAB_ID_RE.fullmatch(lab_id):
        raise HTTPException(
            status_code=422,
            detail="lab_id must contain 1-63 letters, numbers, '.', '_' or '-'.",
        )
    return lab_id


def _lab_lock(lab_id: str) -> asyncio.Lock:
    lock = _lab_locks.get(lab_id)
    if lock is None:
        lock = asyncio.Lock()
        _lab_locks[lab_id] = lock
    return lock


def _api_token() -> str:
    return os.getenv("AIVIRTEACH_API_TOKEN", "")


def _verify_script_layout() -> None:
    missing = [
        str(path)
        for path in (VM_CONTROL_SCRIPT, CREATE_VM_SCRIPT)
        if not path.is_file()
    ]
    if missing:
        raise RuntimeError(f"Required libvirt scripts not found: {', '.join(missing)}")


async def require_api_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(admin_bearer)],
) -> None:
    expected = _api_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AIVIRTEACH_API_TOKEN is not configured.",
        )

    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or not hmac.compare_digest(credentials.credentials, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _error_status(output: str) -> int:
    lowered = output.lower()
    if "vm not found" in lowered or "domain not found" in lowered:
        return status.HTTP_404_NOT_FOUND
    if "already exists" in lowered:
        return status.HTTP_409_CONFLICT
    if "timed out" in lowered:
        return status.HTTP_504_GATEWAY_TIMEOUT
    return status.HTTP_400_BAD_REQUEST


async def run_script(
    argv: Sequence[str | Path],
    *,
    timeout_seconds: int = COMMAND_TIMEOUT_SECONDS,
) -> str:
    env = os.environ.copy()
    env["AIVIRTEACH_NONINTERACTIVE"] = "true"

    process = await asyncio.create_subprocess_exec(
        *(str(item) for item in argv),
        cwd=LIBVIRT_DIR,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.communicate()
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Command timed out after {timeout_seconds} seconds.",
        ) from None

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        detail = stderr_text or stdout_text or f"Command exited with {process.returncode}."
        raise HTTPException(status_code=_error_status(detail), detail=detail)

    return stdout_text


def _parse_key_value_lines(output: str, separator: str = "=") -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        key, found, value = line.partition(separator)
        if found:
            result[key.strip()] = value.strip()
    return result


def _parse_dominfo(output: str) -> dict[str, str]:
    return _parse_key_value_lines(output, separator=":")


@app.on_event("startup")
async def validate_startup() -> None:
    _verify_script_layout()


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/v1/vms",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_token)],
    tags=["vms"],
)
async def create_vm(request: CreateVMRequest) -> dict[str, str | int | bool]:
    lab_id = _validate_lab_id(request.lab_id)
    argv: list[str | Path] = [
        CREATE_VM_SCRIPT,
        lab_id,
        "--memory",
        str(request.memory_mb),
        "--vcpus",
        str(request.vcpus),
    ]
    if request.autostart:
        argv.append("--autostart")

    async with _lab_lock(lab_id):
        output = await run_script(argv, timeout_seconds=CREATE_TIMEOUT_SECONDS)

    parsed = _parse_key_value_lines(output, separator=":")
    return {
        "lab_id": lab_id,
        "username": parsed.get("Username", "learner"),
        "rdp_password": parsed.get("RDP password", ""),
        "rdp_port": 3389,
        "memory_mb": request.memory_mb,
        "vcpus": request.vcpus,
        "autostart": request.autostart,
    }


@app.get(
    "/v1/vms/{lab_id}/status",
    dependencies=[Depends(require_api_token)],
    tags=["vms"],
)
async def vm_status(lab_id: str) -> dict[str, str]:
    lab_id = _validate_lab_id(lab_id)
    output = await run_script([VM_CONTROL_SCRIPT, "status", lab_id])
    return _parse_dominfo(output)


@app.get(
    "/v1/vms/{lab_id}/ip",
    dependencies=[Depends(require_api_token)],
    tags=["vms"],
)
async def vm_ip(lab_id: str) -> dict[str, str]:
    lab_id = _validate_lab_id(lab_id)
    ip_address = await run_script([VM_CONTROL_SCRIPT, "ip", lab_id])
    return {"lab_id": lab_id, "ip_address": ip_address}


@app.get(
    "/v1/vms/{lab_id}/vnc",
    dependencies=[Depends(require_api_token)],
    tags=["vms"],
)
async def vm_vnc(lab_id: str) -> dict[str, str | int]:
    lab_id = _validate_lab_id(lab_id)
    output = await run_script([VM_CONTROL_SCRIPT, "vnc", lab_id])
    parsed = _parse_key_value_lines(output)
    response: dict[str, str | int] = {"lab_id": lab_id, **parsed}
    if "port" in response:
        response["port"] = int(response["port"])
    return response


@app.get(
    "/v1/vms/{lab_id}/credentials",
    dependencies=[Depends(require_api_token)],
    tags=["vms"],
)
async def vm_credentials(lab_id: str) -> dict[str, str | int]:
    lab_id = _validate_lab_id(lab_id)
    output = await run_script([VM_CONTROL_SCRIPT, "credentials", lab_id])
    parsed: dict[str, str | int] = _parse_key_value_lines(output)
    if "rdp_port" in parsed:
        parsed["rdp_port"] = int(parsed["rdp_port"])
    return parsed


@app.post(
    "/v1/vms/{lab_id}/actions/{action}",
    response_model=OperationResponse,
    dependencies=[Depends(require_api_token)],
    tags=["vms"],
)
async def vm_action(lab_id: str, action: VMAction) -> OperationResponse:
    lab_id = _validate_lab_id(lab_id)
    async with _lab_lock(lab_id):
        output = await run_script([VM_CONTROL_SCRIPT, action.value, lab_id])
    return OperationResponse(
        lab_id=lab_id,
        operation=action.value,
        message=output or "Command completed.",
    )


@app.delete(
    "/v1/vms/{lab_id}",
    response_model=OperationResponse,
    dependencies=[Depends(require_api_token)],
    tags=["vms"],
)
async def delete_vm(
    lab_id: str,
    confirm: Annotated[bool, Query(description="Must be true to delete the VM")] = False,
) -> OperationResponse:
    lab_id = _validate_lab_id(lab_id)
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deletion requires confirm=true.",
        )

    async with _lab_lock(lab_id):
        output = await run_script([VM_CONTROL_SCRIPT, "delete", lab_id, "--yes"])
    return OperationResponse(
        lab_id=lab_id,
        operation="delete",
        message=output or "VM deleted.",
    )
