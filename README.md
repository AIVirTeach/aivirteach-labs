# aivirteach-labs
AIVirTeach Labs Runtime — Go + KubeVirt VM 编排, noVNC, Evaluator

labs - 只负责执行不负责状态

## Agent 课程文件

原始课程保存在 `.cache/course/AI Daily Briefing/raw`。运行下面的命令会把 Markdown、检查点、常见故障和文本附件转换为适合 Agent 检索的结构化 JSON：

```bash
.venv/bin/python scripts/process_course.py
```

生成结果：

```text
.cache/course/AI Daily Briefing/processed/course.agent.json
.cache/course/AI Daily Briefing/processed/index.json
```

Agent 默认从该 `processed` 目录检索。部署到其他位置时设置：

```bash
export AIVIRTEACH_COURSE_DIR="/path/to/processed"
```

`aivirteach-server` 仍负责传入当前 `course_id` 和 `lesson_id`；Agent 只用本地课程文件补全步骤说明、预期结果、成功标准、常见故障和相关原文，不会从课程文件扩大诊断工具权限。
重新生成课程文件或修改 `AIVIRTEACH_COURSE_DIR` 后，需要重启 Agent 服务以重新加载课程索引。

使用默认开启深度思考的 OpenAI-compatible 模型时，如果当前 Agent 适配器不需要传递模型的思考过程，可设置：

```bash
export AIVIRTEACH_MODEL_THINKING="disabled"
```

未设置该变量时不会向其他 OpenAI-compatible 服务发送供应商专用参数。

## FastAPI 服务

三个端口各自运行独立的应用，避免高权限 VM 管理、只读调查和模型推理共用同一个进程：

| 端口 | 服务 | 实际职责 |
| --- | --- | --- |
| 8760 | VM Manager + Unified Docs | 执行 VM 管理；统一展示三个服务的 API |
| 8765 | Diagnostic Gateway | 执行固定模板的只读调查工具 |
| 8770 | Agent Service | 结合课程上下文推理并调用 8765 |

在 `http://127.0.0.1:8760/docs` 测试 Diagnostic API 时，Swagger 浏览器会直接请求 8765，并不是由 8760 代理执行。

### 安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 启动

先在终端 1 启动只读 Diagnostic Gateway：

当前用户需要能访问 `qemu:///system`；Ubuntu 本地开发通常将用户加入
`libvirt` 组并重新登录即可，不要求配置免密 sudo。

```bash
export AIVIRTEACH_DIAGNOSTIC_TOKEN="$(openssl rand -hex 32)"
export AIVIRTEACH_DIAGNOSTIC_PORT="8765"
export AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS="http://127.0.0.1:8760,http://localhost:8760"
./start_gateway_service.sh
```

在终端 2 启动 VM Manager 和统一文档：

```bash
export AIVIRTEACH_API_TOKEN="$(openssl rand -hex 32)"
export AIVIRTEACH_DIAGNOSTIC_DOCS_URL="http://127.0.0.1:8765"
export AIVIRTEACH_AGENT_DOCS_URL="http://127.0.0.1:8770"
sudo --preserve-env=AIVIRTEACH_API_TOKEN,AIVIRTEACH_DIAGNOSTIC_DOCS_URL,AIVIRTEACH_AGENT_DOCS_URL \
  ./start_service.sh
```

文档地址：

```text
http://127.0.0.1:8760/docs  # 统一文档
http://127.0.0.1:8765/docs  # Diagnostic Gateway 自身文档
```

### 接口

```text
GET    /health
POST   /v1/vms
GET    /v1/vms/{lab_id}/status
GET    /v1/vms/{lab_id}/ip
GET    /v1/vms/{lab_id}/vnc
GET    /v1/vms/{lab_id}/credentials
POST   /v1/vms/{lab_id}/browser-sessions
POST   /v1/vms/{lab_id}/actions/{start|stop|force-stop|reboot}
DELETE /v1/vms/{lab_id}?confirm=true

POST   /v1/diagnostics/{lab_id}/tools/{tool}  # 实际运行于 8765
POST   /v1/agent/diagnose                     # 实际运行于 8770
```

Swagger 右上角的 **Authorize** 会分别显示 Admin、Session、Diagnostic 和 Agent Bearer Token。

调用示例：

```bash
curl -H "Authorization: Bearer $AIVIRTEACH_API_TOKEN" \
  http://127.0.0.1:8760/v1/vms/lab-001/ip
```

只读诊断调用示例：

```bash
curl -X POST -H "Authorization: Bearer $AIVIRTEACH_DIAGNOSTIC_TOKEN" \
  -H "Content-Type: application/json" -d '{"parameters":{}}' \
  http://127.0.0.1:8765/v1/diagnostics/lab-001/tools/get_vm_status
```

8765 默认只允许来自 `http://127.0.0.1:8760` 和 `http://localhost:8760` 的浏览器跨端口请求。部署到其他域名时，通过 `AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS` 设置精确来源，不要使用通配符。

远程开发访问建议保留 loopback 监听，并同时转发文档端口和诊断端口：

```bash
ssh -N \
  -L 18760:127.0.0.1:8760 \
  -L 18765:127.0.0.1:8765 \
  -L 18770:127.0.0.1:8770 \
  your-user@LIBVIRT_HOST
```

使用上述本地转发端口时，配置浏览器实际可达的地址和来源：

- 8760：`AIVIRTEACH_DIAGNOSTIC_DOCS_URL=http://127.0.0.1:18765`，`AIVIRTEACH_AGENT_DOCS_URL=http://127.0.0.1:18770`
- 8765：`AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS=http://127.0.0.1:18760`
- 8770：`AIVIRTEACH_AGENT_CORS_ORIGINS=http://127.0.0.1:18760`

生产环境应使用 HTTPS 反向代理和防火墙。VM Manager 的环境文件是 `/etc/aivirteach-labs.env`，Diagnostic Gateway 的环境文件是 `/etc/aivirteach-diagnostics.env`。

## 浏览器内嵌 RDP（Guacamole）

`guacamole/compose.yaml` 会启动三个仅绑定到 loopback 的服务：Guacamole Web（8080）、guacd，以及只负责启动已分配 VM 和签发短期票据的 VM Manager（8760）。guacd 的 4822 端口不会发布到宿主机，VM Manager 容器也不会启用管理员 API。

首次启动：

```bash
cd guacamole
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，分别生成 JSON 认证密钥和 server-to-Labs 会话 token：

```bash
openssl rand -hex 16  # GUACAMOLE_JSON_SECRET
openssl rand -hex 32  # AIVIRTEACH_SESSION_TOKEN
```

`AIVIRTEACH_SESSION_TOKEN` 必须与 `aivirteach-server/.env` 中的 `LABS_SESSION_TOKEN` 完全一致。然后启动：

```bash
docker compose up -d --build
docker compose ps
```

开发环境由 client 将同源路径 `/guacamole` 代理到 `http://127.0.0.1:8080`。server 只把 learner 映射到可信 `lab_id`，浏览器不会收到虚拟机明文 IP 或 RDP 密码。

## 查看虚拟机日志

`libvirt/scripts/vm-logs.sh` 用于读取指定虚拟机在宿主机上的 QEMU/libvirt 日志或相关 systemd journal。它不会进入虚拟机内部读取 guest OS 日志。

查看最近 200 行 QEMU 日志：

```bash
./libvirt/scripts/vm-logs.sh lab-001
```

指定行数：

```bash
./libvirt/scripts/vm-logs.sh lab-001 --lines 500
```

查看宿主机 journal 中包含该 VM ID 的记录：

```bash
./libvirt/scripts/vm-logs.sh lab-001 --source journal
```

同时查看两类日志：

```bash
./libvirt/scripts/vm-logs.sh lab-001 --source all --lines 300
```

持续跟踪新日志，使用 `Ctrl+C` 停止：

```bash
./libvirt/scripts/vm-logs.sh lab-001 --source qemu --follow
./libvirt/scripts/vm-logs.sh lab-001 --source journal --follow
```
