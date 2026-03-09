# openclaw-relay-connect

将你的 OpenClaw 连接到中转服务器，让客户端 App 可以远程与之对话。

通过 OpenClaw Gateway 协议（Ed25519 设备签名）连接本地 AI，支持上下文记忆。

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
| `--gateway` | 否 | 本地 OpenClaw Gateway 地址（默认 `ws://127.0.0.1:18789`） |
| `--gateway-token` | 否 | Gateway 认证 Token（如果设置了密码） |
| `--echo` | 否 | 强制 echo 模式（不连接 Gateway，原样返回消息） |

## 环境变量

也可以通过环境变量配置 Gateway：

```bash
export OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
export OPENCLAW_GATEWAY_TOKEN=你的token
```

## 工作原理

1. 用客户端给的 Link Code + Secret 绑定到中转服务器
2. 生成 Ed25519 密钥对，连接本地 OpenClaw Gateway（设备签名认证）
3. 建立 WebSocket 长连接到中转服务器
4. 接收客户端消息 → 转发给 Gateway → 将 AI 回复转发回客户端

不提供 Gateway 地址或连接失败时自动降级为 Echo 模式。

## 后台运行

```bash
nohup python3 -u connect.py \
  --relay https://xxx.railway.app \
  --link-code XXXXXX \
  --secret xxxxxxxx \
  > connector.log 2>&1 &
```
