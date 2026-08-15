# AIVirTeach troubleshooting agent

This repository now contains two security domains:

- `gateway_service.py` runs with libvirt privileges on port 8765. It exposes the
  existing VM lifecycle API under `AIVIRTEACH_API_TOKEN` and a fixed, read-only
  diagnostic API under `AIVIRTEACH_DIAGNOSTIC_TOKEN`.
- `agent_service.py` runs as an unprivileged account on port 8770. It receives a
  normalized course-step snapshot from `aivirteach-server`, asks a replaceable
  model provider what evidence is needed, and calls only the diagnostic API.

The Agent never receives `AIVIRTEACH_API_TOKEN`, VM credentials, a shell tool,
or lifecycle operations. Diagnostic commands are fixed argv templates executed
through QEMU Guest Agent; there is no SSH requirement and no `shell=True` path.

## Start locally

Install the existing Python dependencies once:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Generate two unrelated tokens, then start the combined root gateway:

```bash
export AIVIRTEACH_API_TOKEN="$(openssl rand -hex 32)"
export AIVIRTEACH_DIAGNOSTIC_TOKEN="$(openssl rand -hex 32)"
sudo --preserve-env=AIVIRTEACH_API_TOKEN,AIVIRTEACH_DIAGNOSTIC_TOKEN \
  ./start_gateway_service.sh
```

In another terminal, start the Agent with the same diagnostic token and a
different server-to-Agent token:

```bash
export AIVIRTEACH_AGENT_TOKEN="$(openssl rand -hex 32)"
export AIVIRTEACH_DIAGNOSTIC_TOKEN="the-same-diagnostic-token-as-the-gateway"
export AIVIRTEACH_MODEL_PROVIDER="fake"
./start_agent_service.sh
```

`fake` validates the HTTP integration but intentionally performs no reasoning.
To use a compatible model endpoint:

```bash
export AIVIRTEACH_MODEL_PROVIDER="openai_compatible"
export AIVIRTEACH_MODEL_BASE_URL="https://provider.example/v1"
export AIVIRTEACH_MODEL_API_KEY="..."
export AIVIRTEACH_MODEL_NAME="..."
./start_agent_service.sh
```

The model base URL, key, and name are deployment configuration; clients cannot
override them per request.

## API

Liveness and readiness:

```bash
curl http://127.0.0.1:8770/health
curl http://127.0.0.1:8770/ready
```

Diagnosis endpoint:

```text
POST /v1/agent/diagnose
Authorization: Bearer AIVIRTEACH_AGENT_TOKEN
```

The request must contain `request_id`, `lab_id`, the learner question, compact
course metadata, a normalized `current_step`, learner state, and an explicit
`diagnostic_scope`. The scope is the server/course allowlist; the Agent validates
every model tool call against it before contacting the gateway.

Example request:

```json
{
  "request_id": "a10beac8-d1db-4b1a-8df0-79aa8208e273",
  "lab_id": "lab-001",
  "question": "n8n 打不开，浏览器显示连接被拒绝，为什么？",
  "response_language": "zh-CN",
  "course": {
    "course_id": "n8n-agent-builder",
    "version": 1,
    "title": "Build an AI Daily Briefing with n8n"
  },
  "current_step": {
    "module_id": "runtime-environment",
    "lesson_id": "install-n8n",
    "sequence": 4,
    "title": "Install and Start n8n",
    "instructions": ["Start n8n with Docker Compose."],
    "expected_result": "n8n listens on localhost:5678.",
    "success_criteria": ["The n8n container is running.", "TCP 5678 is listening."]
  },
  "learner_state": {"currentLessonId": "install-n8n"},
  "diagnostic_scope": {
    "workspace_root": "/home/learner/course",
    "allowed_tools": [
      "get_vm_status",
      "get_guest_agent_status",
      "get_guest_service_status",
      "get_guest_journal",
      "list_course_files",
      "read_course_file",
      "list_guest_containers",
      "get_guest_container_status",
      "get_guest_container_logs",
      "check_guest_port"
    ],
    "allowed_relative_paths": [".", "compose.yaml", "docker-compose.yml"],
    "allowed_services": ["docker.service"],
    "allowed_containers": ["n8n"],
    "allowed_ports": [5678],
    "allowed_external_hosts": [],
    "allowed_runtimes": []
  }
}
```

The response is structured as `answer`, `diagnosis`, `course_alignment`, actual
`evidence`, learner-facing `suggested_actions`, `limitations`, and `tool_trace`.
It never executes suggested actions.

## Limits and trust model

- Overall request: 40 seconds by default.
- Model reasoning: at most 4 tool-bearing turns.
- Tool calls: at most 6, including invalid or denied calls.
- Journal and container logs: at most 200 lines per call.
- Course file read: at most 32 KiB, constrained to configured guest roots.
- Sensitive names such as `.env`, `.npmrc`, credentials, and private keys are
  denied. Returned content is also redacted and truncated.
- Logs, files, course text, and learner text are always labeled untrusted and
  cannot add tools or broaden resource allowlists.

The first version supports journal/systemd, networking, bounded course files,
Docker, and Python/Node inspection. It deliberately excludes arbitrary shell,
Docker exec, service restart, package installation, file writes, and VM changes.
