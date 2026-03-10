# openclaw-relay-connect

将你的 OpenClaw 连接到中转服务器，让客户端 App 可以远程与之对话。

## 安全架构

本脚本采用**安全隔离**设计：
- **不直接连接** Gateway WebSocket，不持有任何系统级权限
- 通过 OpenClaw 官方 CLI (`openclaw agent --message`) 安全地发送聊天消息
- 即使中转服务器被攻破，攻击者最多只能发送聊天文本，**无法执行任何命令**
- 中转服务器对所有消息进行白名单校验，只允许纯文本聊天消息通过

## 前置条件

- Python 3.10+
- OpenClaw CLI 已安装并在 PATH 中（`openclaw` 命令可用）

## 快速开始

```bash
git clone https://github.com/HMyaoyuan/openclaw-relay-connect.git
cd openclaw-relay-connect
pip install -r requirements.txt

python3 -u connect.py \
  --relay https://你的中转服务器地址 \
  --link-code 你的LINK_CODE \
  --secret 你的SECRET
```

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `--relay` | 是 | 中转服务器地址 |
| `--link-code` | 是 | 客户端 App 生成的 Link Code |
| `--secret` | 是 | 客户端 App 生成的 Secret |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENCLAW_CLI` | `openclaw` | OpenClaw CLI 可执行文件路径 |

## 工作原理

```
客户端 App  ←→  中转服务器 (Railway)  ←→  connect.py  →  openclaw CLI  →  AI
```

1. 用客户端给的 Link Code + Secret 绑定到中转服务器
2. 建立 WebSocket 长连接到中转服务器，等待客户端消息
3. 收到消息后，调用 `openclaw agent --message "消息内容"` 获取 AI 回复
4. 将纯文本回复推回中转服务器，转发给客户端

找不到 `openclaw` 命令时自动降级为 Echo 模式（原样返回消息）。

## 后台运行

```bash
nohup python3 -u connect.py \
  --relay https://xxx.railway.app \
  --link-code XXXXXX \
  --secret xxxxxxxx \
  > connector.log 2>&1 &
```

## 安全说明

- 脚本只调用 `openclaw agent --message`，这是一个只读聊天接口
- 不使用 Gateway WebSocket 协议，不持有 Ed25519 密钥
- 不请求 `operator.admin`、`operator.approvals` 等高权限 scope
- 中转服务器对消息类型和长度进行严格校验
