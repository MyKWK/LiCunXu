---
summary: "Venus 平台 LLM API 接入指南"
read_when:
  - 需要使用 Venus 平台的 LLM API
  - 需要调用 deepseek、GLM 等模型
  - 需要了解 Venus API 的认证方式
  - 需要将 Venus 作为 OpenClaw 的 LLM provider
title: "Venus LLM API"
---

# Venus 平台 LLM API 接入指南

Venus 是内部 LLM 代理平台，提供统一的 API 接口来调用多种大语言模型（如 GLM-5、DeepSeek 系列等）。
API 兼容 OpenAI Chat Completions 格式，可直接作为 OpenClaw 的自定义 provider 使用。

## 平台地址

- 管理后台：`https://venus.woa.com`
- API Token 管理：`https://venus.woa.com/#/openapi/accountManage/personalAccount`

## API 基本信息

| 配置项       | 值                                                      |
| ------------ | ------------------------------------------------------- |
| **API URL**  | `http://v2.open.venus.oa.com/llmproxy/chat/completions` |
| **请求方式** | `POST`                                                  |
| **协议格式** | OpenAI Chat Completions 兼容                            |

## 认证方式

Venus 使用 **AccessKey** 方式认证：

1. 前往 [Venus Token 管理页面](https://venus.woa.com/#/openapi/accountManage/personalAccount) 获取代理 Token
2. Token 格式为：`AccessKey ID + "@1"`
3. 在请求头中以 Bearer Token 方式传递

```
Authorization: Bearer <AccessKey_ID>@1
```

## 环境变量配置

将 API 凭证写入项目根目录的 `.env` 文件（确保 `.env` 已添加到 `.gitignore`）：

```bash
# Venus LLM API 配置
VENUS_ACCESS_KEY_ID=<你的AccessKey_ID>
VENUS_ACCESS_KEY_SECRET=<你的AccessKey_Secret>
# Venus API Token（AccessKey_ID + @1 格式，供 OpenClaw provider 直接使用）
VENUS_API_TOKEN=<你的AccessKey_ID>@1
```

## 可用模型

| 模型 ID                    | 说明              | 状态   |
| -------------------------- | ----------------- | ------ |
| `glm-5`                    | GLM-5（主力模型） | ✅ 已验证 |
| `deepseek-v3.2`            | DeepSeek V3.2     | ✅ 已验证 |
| `deepseek-v3.1-terminus`   | DeepSeek V3.1     | ✅ 已验证 |
| `minimax-m2.5`             | MiniMax M2.5      | ✅ 已验证 |
| `claude-opus-4-6`          | Claude Opus 4.6   | ✅ 已验证 |
| `gemini-3.1-pro`           | Gemini 3.1 Pro    | ✅ 已验证 |

> 更多可用模型请参考 Venus 平台文档。
> **注意**：模型名必须使用小写，如 `deepseek-v3.2` 而非 `DeepSeek-V3.2`。

## OpenClaw Provider 配置

### 快速配置

在 `~/.openclaw/openclaw.json` 中添加 Venus 作为自定义 provider，将 GLM-5 设为主模型：

```json5
{
  env: {
    VENUS_API_TOKEN: "${VENUS_API_TOKEN}",
  },
  models: {
    providers: {
      venus: {
        baseUrl: "http://v2.open.venus.oa.com/llmproxy",
        apiKey: "VENUS_API_TOKEN",
        api: "openai-completions",
        models: [
          {
            id: "glm-5",
            name: "GLM-5",
            reasoning: false,
            input: ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 202752,
            maxTokens: 16384,
          },
          {
            id: "deepseek-v3.2",
            name: "DeepSeek V3.2",
            reasoning: true,
            input: ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 131072,
            maxTokens: 16384,
          },
          {
            id: "minimax-m2.5",
            name: "MiniMax M2.5",
            reasoning: true,
            input: ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 1048576,
            maxTokens: 16384,
          },
          {
            id: "claude-opus-4-6",
            name: "Claude Opus 4.6",
            reasoning: true,
            input: ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 200000,
            maxTokens: 32768,
          },
          {
            id: "gemini-3.1-pro",
            name: "Gemini 3.1 Pro",
            reasoning: true,
            input: ["text"],
            cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
            contextWindow: 2097152,
            maxTokens: 65536,
          },
        ],
      },
    },
  },
  agents: {
    defaults: {
      model: {
        primary: "venus/glm-5",
        fallbacks: ["venus/deepseek-v3.2", "venus/minimax-m2.5", "venus/claude-opus-4-6", "venus/gemini-3.1-pro"],
      },
      models: {
        "venus/glm-5": { alias: "GLM-5 (Venus)" },
        "venus/deepseek-v3.2": { alias: "DeepSeek V3.2 (Venus)" },
        "venus/minimax-m2.5": { alias: "MiniMax M2.5 (Venus)" },
        "venus/claude-opus-4-6": { alias: "Claude Opus 4.6 (Venus)" },
        "venus/gemini-3.1-pro": { alias: "Gemini 3.1 Pro (Venus)" },
      },
    },
  },
}
```

### 关键说明

- **baseUrl**：使用 `http://v2.open.venus.oa.com/llmproxy`（OpenClaw 会自动追加 `/chat/completions`）
- **apiKey**：引用环境变量 `VENUS_API_TOKEN`，该变量值为 `AccessKey_ID@1` 格式
- **api**：使用 `openai-completions`，因为 Venus 兼容 OpenAI 协议

## Python 调用示例

### 基本调用

```python
import os
import json
import requests
from dotenv import load_dotenv

# 从 .env 文件加载环境变量
load_dotenv()

# 构建 Token：AccessKey ID + "@1"
token = os.environ.get('VENUS_ACCESS_KEY_ID') + "@1"

url = "http://v2.open.venus.oa.com/llmproxy/chat/completions"

payload = {
    'model': 'minimax-m2.5',  # 也支持: glm-5, deepseek-v3.2, deepseek-v3.1-terminus, claude-opus-4-6, gemini-3.1-pro
    'messages': [
        {
            'role': 'system',
            'content': 'You are a helpful assistant.'
        },
        {
            'role': 'user',
            'content': 'Hello'
        }
    ]
}

headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {token}'
}

response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=30)

# 异常处理
if response.status_code != 200:
    print(f"请求失败，状态码: {response.status_code}")
    print(response.json())
    exit()

# 解析返回结果
result = response.json()
content = result['choices'][0]['message']['content']
print(f"模型回复: {content}")
print(f"Token 用量: {result['usage']}")
```

### 返回结果示例

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "model": "deepseek-v3.1-terminus",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 21,
    "completion_tokens": 10,
    "total_tokens": 31
  }
}
```

## 注意事项

1. **网络安全**：如果使用 iOA 安全客户端，需要将 Python/终端程序加入网络白名单，否则会被拦截（返回 403 Forbidden）。
   - 路径：iOA 主界面 → 安全 → 软件访问详情 → 设置访问规则
2. **Token 安全**：AccessKey 不要提交到 Git 仓库，确保 `.env` 在 `.gitignore` 中。
3. **超时设置**：建议设置合理的 `timeout` 参数（推荐 30 秒以上），避免长时间等待。
4. **协议兼容**：API 兼容 OpenAI 格式，可使用 `openai` Python 库，将 `base_url` 指向 Venus 端点。
