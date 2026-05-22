# cc-image-handler

[English](README.md)

Claude Code 插件，拦截图片输入并发送到外部多模态模型进行分析，用模型的文字描述替代 Claude 内置的图片处理。

## 安装

### 方式 1：CLI 命令安装（推荐）

添加 marketplace 源并安装插件：

```bash
claude plugin marketplace add ysq-2024/cc-image-handler
claude plugin install cc-image-handler@cc-image-handler
```

也可以指定安装范围：

```bash
claude plugin install cc-image-handler@cc-image-handler --scope user   # 全局（默认）
claude plugin install cc-image-handler@cc-image-handler --scope project  # 项目级
```

### 方式 2：手动配置

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
    "cc-image-handler@cc-image-handler": true
  }
}
```

然后在 Claude Code 中执行：`/reload-plugins`

### 方式 3：本地目录安装

适合开发或本地测试，使用 `directory` 类型源：

```json
{
  "extraKnownMarketplaces": {
    "cc-image-handler": {
      "source": {
        "source": "directory",
        "path": "/path/to/cc-image-handler"
      }
    }
  },
  "enabledPlugins": {
    "cc-image-handler@cc-image-handler": true
  }
}
```

### 方式 4：一次性测试

```bash
claude --plugin-dir /path/to/cc-image-handler
```

## 依赖

```bash
pip3 install openai anthropic cairosvg Pillow
```

- `openai`：`"format": "openai"` 时必需
- `anthropic`：`"format": "anthropic"` 时必需
- `cairosvg`：可选，启用 SVG 转 PNG。**需要先安装 Cairo 系统库**：
  - Ubuntu/Debian：`sudo apt install libcairo2-dev`
  - macOS：`brew install cairo`
  - Alpine：`apk add cairo-dev`
- `Pillow`：可选，启用 BMP/TIFF/ICO/AVIF/HEIC 转 PNG

## 工作原理

两个 hook 在不同环节拦截图片输入：

| Hook | 事件 | 拦截内容 |
|------|------|----------|
| PreToolUse/Read | Claude 读取图片文件 | 阻止 Read，注入多模态描述 |
| UserPromptSubmit | 用户在 prompt 中引用图片路径 | 检测路径，添加分析结果作为上下文 |

拦截图片后的处理流程：
1. 将不支持的格式（SVG 用 cairosvg，BMP/TIFF 用 Pillow）转为 PNG
2. Base64 编码图片
3. 用 openai 或 anthropic SDK 调用配置的多模态 API
4. 将模型的文字描述注入 Claude 的上下文

如果 API 调用失败，自动回退到 Claude 内置的图片处理。

## 配置

插件从 `~/.claude/settings.json` 的 env 部分读取配置。基础变量是 Claude Code 当前使用的值。覆盖变量允许为图片分析单独指定不同的模型/端点。

| 变量 | 用途 | 必需 |
|------|------|------|
| `ANTHROPIC_BASE_URL` | API 端点 URL（基础） | 是 |
| `ANTHROPIC_API_KEY` | API 密钥（基础） | 是 |
| `ANTHROPIC_MODEL` | 模型名称（基础） | 是 |
| `MULTIMODAL_BASE_URL` | 覆盖图片分析的 URL | 否 |
| `MULTIMODAL_API_KEY` | 覆盖图片分析的 API 密钥 | 否 |
| `MULTIMODAL_MODEL` | 覆盖图片分析的模型 | 否 |
| `MULTIMODAL_FORMAT` | 覆盖 API 格式：`"anthropic"` 或 `"openai"` | 否 |

覆盖变量优先于基础变量。如果 Claude Code 的模型不支持视觉，设置 `MULTIMODAL_*` 指定一个视觉模型。

API 格式默认 `"anthropic"`。如果 URL 含 `/compatible-mode` 则自动检测为 `"openai"`。设置 `MULTIMODAL_FORMAT` 可强制指定。

**最小配置（Claude Code 和图片分析使用同一模型）：**
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/apps/anthropic",
    "ANTHROPIC_API_KEY": "YOUR_API_KEY_HERE",
    "ANTHROPIC_MODEL": "qwen-vl-plus"
  }
}
```

**多模态覆盖配置（Claude Code 用非视觉模型，图片分析用视觉模型）：**
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/apps/anthropic",
    "ANTHROPIC_API_KEY": "YOUR_API_KEY_HERE",
    "ANTHROPIC_MODEL": "glm-5.1",
    "MULTIMODAL_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "MULTIMODAL_MODEL": "qwen-vl-plus",
    "MULTIMODAL_FORMAT": "openai"
  }
}
```

请使用支持视觉的模型（如 `qwen-vl-plus`、`gpt-4o`）进行图片分析。图片始终以 base64 格式上传。

## 支持的图片格式

PNG、JPG、JPEG、GIF、BMP、WebP、SVG、TIFF、ICO、AVIF、HEIC/HEIF

视觉 API 不直接支持的格式（SVG、BMP、TIFF 等）会在发送前自动转换为 PNG。如果所需的转换库（cairosvg 或 Pillow）不可用，这些格式会回退到 Claude 内置的图片处理。