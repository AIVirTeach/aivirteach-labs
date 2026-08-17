# aivirteach-labs
AIVirTeach Labs Runtime — Go + KubeVirt VM 编排, noVNC, Evaluator

labs - 只负责执行不负责状态

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

```bash
export AIVIRTEACH_DIAGNOSTIC_TOKEN="$(openssl rand -hex 32)"
export AIVIRTEACH_DIAGNOSTIC_PORT="8765"
export AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS="http://127.0.0.1:8760,http://localhost:8760"
sudo --preserve-env=AIVIRTEACH_DIAGNOSTIC_TOKEN,AIVIRTEACH_DIAGNOSTIC_PORT,AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS \
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
POST   /v1/vms/{lab_id}/actions/{start|stop|force-stop|reboot}
DELETE /v1/vms/{lab_id}?confirm=true

POST   /v1/diagnostics/{lab_id}/tools/{tool}  # 实际运行于 8765
POST   /v1/agent/diagnose                     # 实际运行于 8770
```

Swagger 右上角的 **Authorize** 会分别显示 Admin、Diagnostic 和 Agent 三种 Bearer Token。

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
