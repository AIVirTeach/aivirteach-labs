# aivirteach-labs
AIVirTeach Labs Runtime — Go + KubeVirt VM 编排, noVNC, Evaluator

labs - 只负责执行不负责状态

## Agent 课程文件

原始课程保存在 `agent-service/.cache/course/AI Daily Briefing/raw`。运行下面的命令会把 Markdown、检查点、常见故障和文本附件转换为适合 Agent 检索的结构化 JSON：

```bash
.venv/bin/python agent-service/scripts/process_course.py
```

生成结果：

```text
agent-service/.cache/course/AI Daily Briefing/processed/course.agent.json
agent-service/.cache/course/AI Daily Briefing/processed/index.json
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

四个端口各自运行独立的应用，避免高权限 VM 管理、只读调查、模型推理和文档展示共用同一个进程：

| 端口 | 服务 | 实际职责 |
| --- | --- | --- |
| 8760 | VM Manager | 只执行 VM 管理并提供自身 OpenAPI |
| 8765 | Diagnostic Gateway | 执行固定模板的只读调查工具 |
| 8770 | Agent Service | 结合课程上下文推理并调用 8765 |
| 8780 | Unified Docs Service | 聚合三个服务的 OpenAPI，不执行服务操作 |

在 `http://127.0.0.1:8780/docs` 使用 **Try it out** 时，Swagger 浏览器会按照每个操作声明的地址直接请求 8760、8765 或 8770；8780 不代理这些操作。

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
export AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS="http://127.0.0.1:8780,http://localhost:8780"
./diagnostic-gateway/start_diagnostic_service.sh
```

在终端 2 启动 VM Manager：

```bash
export AIVIRTEACH_API_TOKEN="$(openssl rand -hex 32)"
sudo --preserve-env=AIVIRTEACH_API_TOKEN \
  ./vm-manager/start_service.sh
```

在终端 3 启动 Agent Service。它使用与 Diagnostic Gateway 相同的诊断 token，并使用独立的 Agent token：

```bash
export AIVIRTEACH_AGENT_TOKEN="$(openssl rand -hex 32)"
export AIVIRTEACH_DIAGNOSTIC_TOKEN="与 Diagnostic Gateway 完全相同的 token"
export AIVIRTEACH_GATEWAY_URL="http://127.0.0.1:8765"
export AIVIRTEACH_MODEL_PROVIDER="fake"
./agent-service/start_agent_service.sh
```

在终端 4 启动不带任何服务 Token 的 Unified Docs Service：

```bash
./docs-service/start_docs_service.sh
```

文档地址：

```text
http://127.0.0.1:8780/docs  # 三个服务的统一文档（Swagger 资源本地托管）
http://127.0.0.1:8760/docs  # VM Manager 自身文档
http://127.0.0.1:8765/docs  # Diagnostic Gateway 自身文档
http://127.0.0.1:8770/docs  # Agent Service 自身文档
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

三个运行时服务默认只允许来自 `http://127.0.0.1:8780` 和 `http://localhost:8780` 的统一文档页面跨端口请求。部署到其他域名时，通过各服务的 `AIVIRTEACH_*_CORS_ORIGINS` 设置精确来源，不要使用通配符。

这里的 CORS 只作用于 8760/8765/8770 的 FastAPI HTTP 接口，主要用于统一 Swagger 页面跨端口执行请求。它不是 Guacamole 8080 的 iframe/WebSocket 配置。正常学习链路由 `aivirteach-server` 在服务端调用 8760，不受浏览器 CORS 限制，因此不要为了远程桌面把 Client 公网 Origin 加进 `AIVIRTEACH_VM_CORS_ORIGINS`，更不要设置成 `*`。

远程开发访问建议保留 loopback 监听，并同时转发文档端口和诊断端口：

```bash
ssh -N \
  -L 18760:127.0.0.1:8760 \
  -L 18765:127.0.0.1:8765 \
  -L 18770:127.0.0.1:8770 \
  -L 18780:127.0.0.1:8780 \
  your-user@LIBVIRT_HOST
```

使用上述本地转发端口时，Docs Service 仍从主机内部的 8760/8765/8770 获取 schema，但需要把 Swagger 操作地址改成浏览器实际可达的转发地址：

- 8780：`AIVIRTEACH_VM_DOCS_URL=http://127.0.0.1:18760`、`AIVIRTEACH_DIAGNOSTIC_DOCS_URL=http://127.0.0.1:18765`、`AIVIRTEACH_AGENT_DOCS_URL=http://127.0.0.1:18770`
- 8760：`AIVIRTEACH_VM_CORS_ORIGINS=http://127.0.0.1:18780`
- 8765：`AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS=http://127.0.0.1:18780`
- 8770：`AIVIRTEACH_AGENT_CORS_ORIGINS=http://127.0.0.1:18780`

生产环境应使用 HTTPS 反向代理和防火墙。环境文件分别是 `/etc/aivirteach-labs.env`、`/etc/aivirteach-diagnostics.env`、`/etc/aivirteach-agent.env` 和 `/etc/aivirteach-docs.env`。

## 浏览器内嵌 RDP（Guacamole）

### 当前架构决定

当前 `local-demo` / `vm_agent_local` 代码统一采用同源 Guacamole 路径，不使用旧 PR 中的 `IronRDP + websockify + labs-console.<domain>` 浏览器直连方案：

```text
Browser / Tauri
  └─ https://learn.<domain>/workspace
      ├─ POST /api/v1/me/lab/session
      └─ /guacamole/?data=<短期不透明票据>
           └─ 同源反向代理 / Cloudflare Tunnel
               └─ Guacamole Web :8080
                   └─ guacd :4822
                       └─ VM :3389
```

Server 返回相对地址 `/guacamole/?data=...`，Client 也只接受这个相对同源合约。当前部署不需要 `LABS_CONSOLE_WS_URL`、websockify 或公开的 `labs-console.<domain>`。如果旧分支仍需要这些组件，应单独维护旧分支文档，不要与这里的 Guacamole 配置混装。

`vm-manager/guacamole/compose.yaml` 会启动三个仅绑定到 loopback 的服务：Guacamole Web（8080）、guacd，以及 VM Manager（8760）。Docker 版 VM Manager 不是在容器里运行嵌套虚拟机：它通过只挂载进容器的宿主机 `/var/run/libvirt/libvirt-sock` 连接宿主机 `qemu:///system`，所以 API 创建出来的 domain 会出现在宿主机的 `virsh --connect qemu:///system list --all` 中。

为了让 `POST /v1/vms` 真正可用，容器镜像包含 `qemu-img`、`cloud-localds` 和 `virt-install`，并把下面两个宿主机目录以相同绝对路径读写挂载：

```text
/var/lib/libvirt/images/aivirteach  VM golden image、overlay 和 cloud-init seed
/var/lib/aivirteach-labs           VM 凭据和运行状态
```

容器还只额外保留 `CHOWN`、`FOWNER` capability，并只读挂载宿主机 `/etc/passwd`、`/etc/group`，让脚本生成的磁盘保持宿主机 `libvirt-qemu:kvm` 所需的实际 UID/GID。因为“libvirt socket + 可写 VM 存储”相当于把宿主机虚拟化管理权交给该容器，8760 必须保持 loopback/私网并使用强管理 Token，不能把管理员 API 直接暴露到公网。

首次启动：

```bash
cd vm-manager/guacamole
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

Docker 版启动后不要同时运行 `vm-manager/start_service.sh`，否则两个 VM Manager 会争用宿主机 8760。先验证容器确实连到宿主机 libvirt，并具备创建工具和写入目录：

```bash
docker compose exec vm-manager virsh --connect qemu:///system list --all
docker compose exec vm-manager sh -lc \
  'command -v qemu-img && command -v cloud-localds && command -v virt-install'
docker compose exec vm-manager test -w /var/lib/libvirt/images/aivirteach/labs
docker compose exec vm-manager test -w /var/lib/aivirteach-labs
```

调用创建接口后，在宿主机确认 VM，而不是进入容器寻找 QEMU 进程：

```bash
curl -X POST http://127.0.0.1:8760/v1/vms \
  -H "Authorization: Bearer $AIVIRTEACH_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"lab_id":"lab-003","memory_mb":4096,"vcpus":2,"autostart":false}'

sudo virsh --connect qemu:///system dominfo lab-003
```

开发环境由 client 将同源路径 `/guacamole` 代理到 `http://127.0.0.1:8080`。server 只把 learner 映射到可信 `lab_id`，浏览器不会收到虚拟机明文 IP 或 RDP 密码。

Tauri 桌面版也使用同一条 Guacamole 链路：桌面壳加载已部署的 React Workspace，生产入口必须在同一个 HTTPS Origin 下把 `/guacamole/` 反向代理到这里的 `127.0.0.1:8080/guacamole/`，并保留 HTTP/1.1 WebSocket Upgrade、关闭 buffering、设置长连接 timeout。不要公开 8760、guacd 4822 或 VM 3389，也不要把 `LABS_SESSION_TOKEN` 或 `GUACAMOLE_JSON_SECRET` 打进桌面安装包。

### Cloudflare Tunnel 开放方式

推荐只给用户开放一个 HTTPS hostname，例如 `learn.example.com`。让该 hostname 先到达同源 gateway（例如 Labs 主机上的 Nginx），再由 gateway 按 path 分发：

```text
learn.example.com
  └─ Cloudflare Tunnel → http://127.0.0.1:8090
      ├─ /                  → React Client
      ├─ /api/v1/           → AIVirTeach Server
      └─ /guacamole/        → http://127.0.0.1:8080/guacamole/
```

本地管理 Tunnel 的最小 `/etc/cloudflared/config.yml` 示例：

```yaml
tunnel: <TUNNEL_UUID>
credentials-file: /etc/cloudflared/<TUNNEL_UUID>.json

ingress:
  - hostname: learn.example.com
    service: http://127.0.0.1:8090
  - service: http_status:404
```

然后为 hostname 建 DNS route 并启动 Tunnel：

```bash
cloudflared tunnel route dns <TUNNEL_UUID或名称> learn.example.com
cloudflared tunnel ingress validate
sudo systemctl enable --now cloudflared
```

Cloudflare 也推荐通过 Dashboard 创建 remotely-managed Tunnel；无论采用哪种管理方式，公开路由都应指向同源 gateway，而不是重新开放旧的 `labs-console.<domain>:6080`。Cloudflare Tunnel 支持把公网 hostname 映射到本地 HTTP 服务，并透明承载 HTTP WebSocket；gateway 仍必须保留 `Upgrade`/`Connection` 请求头、关闭 buffering 并配置长连接 timeout。参考 [Cloudflare Tunnel Routing](https://developers.cloudflare.com/tunnel/routing/) 和 [本地配置文件说明](https://developers.cloudflare.com/tunnel/advanced/local-management/configuration-file/)。

如果 React/Server 部署在 Vercel、Cloudflare Pages 或其他主机，那个环境的 `127.0.0.1:8080` 不是 Labs。此时应由 `learn.example.com` 前面的 edge/path router 把 `/guacamole/` 转发到 Labs Tunnel origin；浏览器看到的地址仍然必须是 `https://learn.example.com/guacamole/...`。可以给 Server-to-Labs 的 8760 另建受 Cloudflare Access Service Token 保护的私有 hostname，但浏览器 `/guacamole/` 路径不能依赖需要暴露机器 Service Token 的认证方式。

公网入口不要暴露以下端口：

```text
8760  VM Manager
4822  guacd
3389  VM RDP
```

Guacamole iframe 通常不依赖 VM Manager CORS；需要检查的是反向代理 WebSocket、CSP `frame-src`、响应 `frame-ancestors` 和 `X-Frame-Options`。外部联调时，在浏览器 Network 中确认 Guacamole tunnel 成功升级为 HTTP 101。

安全检查：当前 Compose 配置还包含浏览器会话以外的高权限 Token 注入，且旧版本可能曾把值直接写进受版本控制文件。生产重启前要先确认哪些管理接口仍在使用它们，把需要保留的值迁移到不提交 Git 的 `.env`/secret store，并轮换所有曾进入 Git 或日志的 Token。不要只删除文件中的一行就假设旧值已失效。

## 查看虚拟机日志

`vm-manager/libvirt/scripts/vm-logs.sh` 用于读取指定虚拟机在宿主机上的 QEMU/libvirt 日志或相关 systemd journal。它不会进入虚拟机内部读取 guest OS 日志。

查看最近 200 行 QEMU 日志：

```bash
./vm-manager/libvirt/scripts/vm-logs.sh lab-001
```

指定行数：

```bash
./vm-manager/libvirt/scripts/vm-logs.sh lab-001 --lines 500
```

查看宿主机 journal 中包含该 VM ID 的记录：

```bash
./vm-manager/libvirt/scripts/vm-logs.sh lab-001 --source journal
```

同时查看两类日志：

```bash
./vm-manager/libvirt/scripts/vm-logs.sh lab-001 --source all --lines 300
```

持续跟踪新日志，使用 `Ctrl+C` 停止：

```bash
./vm-manager/libvirt/scripts/vm-logs.sh lab-001 --source qemu --follow
./vm-manager/libvirt/scripts/vm-logs.sh lab-001 --source journal --follow
```

## 服务文件分布

仓库根目录只保留三个服务共同使用的依赖、部署文件和测试；每套服务的入口、实现、专属配置及资源放在自己的目录中。

```text
aivirteach-labs/
├── vm-manager/                 # 8760：VM 管理
│   ├── start_service.sh        # 本地/systemd 启动入口
│   ├── service.py              # VM 生命周期与浏览器 RDP 会话
│   ├── config/                 # VM Manager 环境变量示例
│   ├── libvirt/                # VM 镜像、创建、控制与日志脚本
│   └── guacamole/              # 浏览器内嵌 RDP 的 Compose 配置
├── diagnostic-gateway/         # 8765：特权、只读诊断网关
│   ├── start_diagnostic_service.sh
│   ├── gateway_service.py      # FastAPI 应用入口、CORS、健康检查
│   ├── diagnostic_gateway.py   # 固定诊断工具与 QEMU Guest Agent 调用
│   └── config/                 # Diagnostic 环境变量示例
├── agent-service/              # 8770：课程感知的排障 Agent
│   ├── start_agent_service.sh
│   ├── agent_service.py        # FastAPI 应用入口
│   ├── aivirteach_agent/       # 编排、工具策略、模型适配与课程检索
│   ├── scripts/                # 课程转换工具
│   ├── config/                 # Agent 环境变量示例
│   └── .cache/course/          # 原始和处理后的课程文件（不提交 Git）
├── docs-service/               # 8780：无特权统一 API 文档
│   ├── start_docs_service.sh
│   ├── docs_service.py         # 文档页面、健康检查和 OpenAPI 端点
│   ├── openapi_aggregator.py   # 通过 HTTP 获取并合并三个服务 schema
│   ├── static/                 # 本地 Swagger UI JS/CSS/图标
│   └── config/                 # Docs Service 环境变量示例
├── systemd/                    # 四个服务的 systemd unit
├── tests/                      # 跨服务 Python 和 Shell 测试
└── requirements.txt            # 三个服务共享的 Python 依赖
```

| 服务 | 入口 | 环境变量示例 | 主要专属依赖 |
| --- | --- | --- | --- |
| VM Manager | `vm-manager/start_service.sh` | `vm-manager/config/api.env.example` | `vm-manager/libvirt/`、`vm-manager/guacamole/` |
| Diagnostic Gateway | `diagnostic-gateway/start_diagnostic_service.sh` | `diagnostic-gateway/config/diagnostics.env.example` | libvirt socket、QEMU Guest Agent |
| Agent Service | `agent-service/start_agent_service.sh` | `agent-service/config/agent.env.example` | `agent-service/aivirteach_agent/`、`agent-service/.cache/course/` |
| Unified Docs | `docs-service/start_docs_service.sh` | `docs-service/config/docs.env.example` | 三个服务的 `/openapi.json`、本地 Swagger UI 静态资源 |

服务间调用方向：

```text
aivirteach-server ──► Agent Service :8770 ──► Diagnostic Gateway :8765 ──► VM

aivirteach-server ──► VM Manager :8760 ──► libvirt

Docs Service :8780 ──HTTP GET──► :8760/:8765/:8770 /openapi.json
```

Docs Service 不 import 三个运行时服务的 Python 实现，也不持有服务 Token。某个服务暂时离线时，它会继续展示上一次成功读取的 schema 并标记为 `stale`；从未读取成功的服务会标记为 `unavailable`。
