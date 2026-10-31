# Proxy Protocol Adapter

Proxy Protocol Adapter 是一个面向 Windows 的 **适配器**，用于让不支持 PROXY protocol v2 协议的 UDP 服务也能够正确获取真实IP地址。

反向代理软件（例如 frp）在转发 UDP 时，允许将真实客户端 IP 写入 PROXY v2 头，大部分 UDP 服务（例如游戏服务端）无法识别。该程序在 Windows 网络层截获这些数据，解析 PROXY v2 头、丢弃头本身，并把源地址改写为真实客户端 IP，使目标 UDP 服务无需修改就能看到真实来源。

当前只测试了 **frp**。其他支持 PROXY protocol v2 的反向代理软件理论上也可能适用，但需要自行验证。

> [!CAUTION]
> WinDivert 驱动可能被部分杀毒软件、EDR 或安全软件拦截
> 如果确认使用的是本程序自带的 WinDivert 驱动（由pydivert包提供），请在杀毒软件中放行并信任程序
> 不要随意下载来源不明的 WinDivert 驱动。
## 适用范围

- 目标服务必须是 **UDP 服务**；
- 反向代理必须支持并开启 PROXY protocol v2；
- 当前程序只测试了 frp 的 UDP 隧道；
- 目前主要面向 Windows x64。

## 工作原理

```text
客户端
  |
  v
frps / 其他反向代理
  |
  v
frpc -> 127.0.0.1:<目标端口>
           |
           | 发送 PROXY v2 头 + UDP 数据
           v
     Proxy Protocol Adapter
       - 识别并解析 PROXY v2 头
       - 丢弃头，不交给目标服务
       - 记录 frp 端点 -> 真实客户端 IP
       - 把 UDP 数据源地址改写为真实 IP
           |
           v
      目标 UDP 服务

目标服务回包
  |
  v
Proxy Protocol Adapter
  - 把回包目标地址改回 frp 端点
  - 把回包源地址改为 reply_source
  |
  v
frpc -> frps -> 客户端
```

## 安装与运行

### 方式一：直接运行 exe

```text
dist\ProxyProtocolAdapter.exe
```

程序需要管理员权限，启动后会弹出 UAC 提示。

### 方式二：从源码运行

依赖：

```powershell
python -m pip install -r requirements.txt
```

运行 GUI：

```powershell
python proxy_nat_gui.py
```

运行 CLI：

```powershell
python proxy_nat.py
```

## 目录结构

程序启动时会在 exe 同目录创建：

```text
ProxyProtocolAdapter.exe
config\
  └─ proxy_nat.json
logs\
  ├─ proxy_nat.log
  ├─ proxy_nat_stderr.log
  └─ proxy_nat.pid
```

## 配置

配置文件：

```text
config\proxy_nat.json
```

支持多条 NAT 规则：

```json
{
  "log": "logs/proxy_nat.log",
  "timeout": 300.0,
  "rules": [
    {
      "name": "NAT 1",
      "enabled": true,
      "target_host": "127.0.0.1",
      "target_port": 27021,
      "reverse_proxy_source": "127.0.0.1",
      "reply_source": "127.0.0.1"
    },
    {
      "name": "NAT 2",
      "enabled": true,
      "target_host": "127.0.0.1",
      "target_port": 27022,
      "reverse_proxy_source": "127.0.0.1",
      "reply_source": "127.0.0.1"
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `log` | 日志文件，相对程序目录 |
| `timeout` | NAT 会话空闲超时，单位秒 |
| `rules[].name` | 规则名称，显示在 GUI 和日志中 |
| `rules[].enabled` | 是否启用该规则 |
| `rules[].target_host` | 目标服务地址 |
| `rules[].target_port` | 目标 UDP 端口 |
| `rules[].reverse_proxy_source` | 反向代理发往目标服务时使用的源地址 |
| `rules[].reply_source` | 返回给反向代理时使用的源地址 |

### reverse_proxy_source 与 reply_source

- `reverse_proxy_source`：用于匹配反向代理发往目标的 UDP 包；
- `reply_source`：用于伪造目标回包的源地址，让反向代理认为回包来自它连接的目标地址。

典型的 frp 场景通常两者都是：

```text
127.0.0.1
```

如果 frpc 使用其他 `localIP`，或者经过本地端口转发、多网卡环境，两者可能不同。

## frp 配置示例

让 frpc 的 UDP 隧道指向目标端口，并开启 PROXY protocol v2：

```toml
[[proxies]]
name = "udp-service"
type = "udp"
localIP = "127.0.0.1"
localPort = 27021
remotePort = 12345
transport.proxyProtocolVersion = "v2"
```

配置完成后：

```text
远程客户端 -> frps -> frpc -> 127.0.0.1:27021 -> Proxy Protocol Adapter -> 目标 UDP 服务
```

## GUI

GUI 提供：

- 多条 NAT 规则的增删改、启用/禁用；
- 目标地址、目标端口、反向代理源地址、回包源地址配置；
- 启动、停止、重启；
- WinDivert 驱动状态显示；
- 日志实时显示；
- 关闭窗口最小化到托盘；
- 托盘右键菜单：关闭 GUI、彻底退出。

窗口关闭不会停止 NAT，NAT 会继续在托盘运行。

## 构建 exe

安装构建依赖：

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller
```

执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

生成：

```text
dist\ProxyProtocolAdapter.exe
```

构建脚本会自动复制 `config\proxy_nat.json` 和 `README.md` 到 `dist`。


## 限制

- 当前只支持 UDP，不支持 TCP 透明 NAT；
- 目标服务必须运行在本机可访问的 UDP 端口；
- 反向代理必须正确发送 PROXY v2 头；
- 需要管理员权限；
- WinDivert 驱动与 Windows 版本、杀毒软件可能存在兼容性问题；
- 其他反向代理软件未经过完整测试。
