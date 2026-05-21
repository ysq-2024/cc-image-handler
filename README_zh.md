# multimodal-image-handler

[English](README.md)

Claude Code 插件，拦截图片输入并发送到外部多模态模型进行分析，用模型的文字描述替代 Claude 内置的图片处理。

## 安装

### 方式 1：Slash 命令安装（推荐）

在 `~/.claude/settings.json` 中添加 marketplace 源并启用插件：

```json
{
  "extraKnownMarketplaces": {
    "cc-image-handler": {
      "source": {
        "source": "github",
        "repo": "ysq-2024/cc-image-handler"
      }
    }
  },
  "enabledPlugins": {
    "multimodal-image-handler@cc-image-handler": true
  }
}
```

然后在 Claude Code 中执行：`/reload-plugins`

### 方式 2：本地目录安装

适合开发或本地测试，使用 `directory` 类型源：

```json
{
  "extraKnownMarketplaces": {
    "cc-image-handler": {
      "source": {
        "source": "directory",
        "path": "/path/to/multimodal-image-handler"
      }
    }
  },
  "enabledPlugins": {
    "multimodal-image-handler@cc-image-handler": true
  }
}
```

### 方式 3：一次性测试

```bash
claude --plugin-dir /path/to/multimodal-image-handler
```

## 依赖

```bash
pip3 install openai anthropic cairosvg Pillow
```

- `openai`：`"format": "openai"` 时必需
- `anthropic`：`"format": "anthropic"` 时必需
- `cairosvg`：可选，启用 SVG 转 PNG
- `Pillow`：可选，启用 BMP/TIFF/ICO/AVIF/HEIC 转 PNG

## 工作原理

三个 hook 在不同环节拦截图片输入：

| Hook | 事件 | 拦截内容 |
|------|------|----------|
| PreToolUse/Read | Claude 读取图片文件 | 阻止 Read，注入多模态描述 |
| UserPromptSubmit | 用户在 prompt 中引用图片路径 | 检测路径，添加分析结果作为上下文 |
| PostToolUse/Bash | Bash 命令产生了图片文件 | 扫描输出中的路径，添加分析结果 |

拦截图片后的处理流程：
1. 将不支持的格式（SVG 用 cairosvg，BMP/TIFF 用 Pillow）转为 PNG
2. Base64 编码图片
3. 用 openai 或 anthropic SDK 调用配置的多模态 API
4. 将模型的文字描述注入 Claude 的上下文

如果 API 调用失败，自动回退到 Claude 内置的图片处理。

## 配置

插件**默认使用 Claude Code 当前的模型配置**（相同的 URL、API key 和模型），无需额外配置。

配置优先级（高 → 低）：
1. `~/.claude/multimodal-config.json` — 显式覆盖（任意字段）
2. Claude Code 环境变量 — `ANTHROPIC_BASE_URL`、`ANTHROPIC_AUTH_TOKEN`、`ANTHROPIC_MODEL`
3. 自动检测 — API 格式从 URL 模式推断

**最小配置**（只覆盖需要不同的字段，比如换一个视觉模型）：
```json
{
  "model": "qwen-vl-plus"
}
```

**完整配置**（覆盖所有字段）：
```json
{
  "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
  "apiKey": "YOUR_API_KEY_HERE",
  "model": "qwen-vl-plus",
  "format": "openai",
  "prompt": "请详细描述这张图片...",
  "timeout": 60
}
```

**字段说明：**
- `url`：API 端点。默认取 `ANTHROPIC_BASE_URL`。对于 OpenAI 兼容提供商，自动剥离 `/chat/completions` 后缀。
- `apiKey`：API 密钥。默认取 `ANTHROPIC_AUTH_TOKEN`。
- `model`：模型名称。默认取 `ANTHROPIC_MODEL`。请使用支持视觉的模型（如 `qwen-vl-plus`、`gpt-4o`）。
- `format`：`"openai"` 或 `"anthropic"`。根据 URL 自动检测：含 `/compatible-mode` → openai，其余 → anthropic。
- `prompt`：随每张图片一起发送给模型的提示词。
- `timeout`：请求超时秒数（默认 60）。

参考 `hooks/scripts/multimodal-config.example.json` 中的最小模板。

## 支持的图片格式

PNG、JPG、JPEG、GIF、BMP、WebP、SVG、TIFF、ICO、AVIF、HEIC/HEIF

视觉 API 不直接支持的格式（SVG、BMP、TIFF 等）会在发送前自动转换为 PNG。