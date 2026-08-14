# aivirteach-labs
AIVirTeach Labs Runtime — Go + KubeVirt VM 编排, noVNC, Evaluator

labs - 只负责执行不负责状态

## FastAPI VM 管理服务

服务位于仓库根目录的 `service.py`，只允许调用固定的 libvirt 操作，不接受任意 Shell 命令。默认使用本仓库的 `libvirt/scripts`，监听 `127.0.0.1:8765`，VM 接口必须携带 Bearer Token。

### 安装依赖

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 启动

```bash
export AIVIRTEACH_API_TOKEN="$(openssl rand -hex 32)"
export AIVIRTEACH_API_HOST="127.0.0.1"
export AIVIRTEACH_API_PORT="8765"

sudo --preserve-env=AIVIRTEACH_API_TOKEN,AIVIRTEACH_API_HOST,AIVIRTEACH_API_PORT \
  ./start_service.sh
```

API 文档：

```text
http://127.0.0.1:8765/docs
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
```

调用示例：

```bash
curl -H "Authorization: Bearer $AIVIRTEACH_API_TOKEN" \
  http://127.0.0.1:8765/v1/vms/lab-001/ip
```

远程开发访问建议保留 loopback 监听并建立 SSH 隧道：

```bash
ssh -N -L 18765:127.0.0.1:8765 your-user@LIBVIRT_HOST
```

生产环境应使用 HTTPS 反向代理和防火墙。systemd 模板位于 `systemd/aivirteach-labs.service`，默认部署目录是 `/opt/aivirteach-labs`，环境文件是 `/etc/aivirteach-labs.env`。

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
