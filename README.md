# openclaw-relay-connect

将你的 OpenClaw 连接到中转服务器，让客户端 App 可以远程与之对话。

## 安全架构

本脚本采用**安全隔离**设计：
- **不直接连接** Gateway WebSocket，不持有任何系统级权限
- 通过 OpenClaw 官方 CLI (`openclaw send --label`) 安全地发送聊天消息
- 使用 `--label` 绑定专属会话，自动维护上下文和记忆
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
| `--label` | 否 | OpenClaw 会话标签，用于隔离上下文（默认: `mobile-app`） |

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENCLAW_CLI` | `openclaw` | OpenClaw CLI 可执行文件路径 |
| `OPENCLAW_SESSION_LABEL` | `mobile-app` | 默认会话标签 |

## 工作原理

```
客户端 App  ←→  中转服务器 (Railway)  ←→  connect.py  →  openclaw CLI  →  AI
```

1. 用客户端给的 Link Code + Secret 绑定到中转服务器
2. 建立 WebSocket 长连接到中转服务器，等待客户端消息
3. 收到消息后，调用 `openclaw send --label "mobile-app" "消息内容"` 发送给 AI
4. AI 的回复通过同一个 session 返回，自动保持上下文连贯
5. 将纯文本回复推回中转服务器，转发给客户端

找不到 `openclaw` 命令时自动降级为 Echo 模式（原样返回消息）。

## 上下文与记忆

- **上下文自动串联**：同一个 `--label` 下的所有消息共享同一个会话历史，AI 能回忆之前的对话
- **SOUL / IDENTITY 保持**：无论消息从哪个渠道来，OpenClaw 都会加载完整的人设和灵魂
- **MEMORY 正常工作**：AI 的长期记忆、笔记等功能不受影响
- **多客户端隔离**：不同 `--label` 的会话互不干扰，手机 App 的聊天不会影响终端主会话

## 后台运行

```bash
nohup python3 -u connect.py \
  --relay https://xxx.railway.app \
  --link-code XXXXXX \
  --secret xxxxxxxx \
  > connector.log 2>&1 &
```

## 安全说明

- 脚本只调用 `openclaw send --label`，这是一个受限的聊天接口
- 不使用 Gateway WebSocket 协议，不持有 Ed25519 密钥
- 不请求 `operator.admin`、`operator.approvals` 等高权限 scope
- 中转服务器对消息类型和长度进行严格校验（上限 50KB）
