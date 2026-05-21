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

插件从 `~/.claude/settings.json` 的 env 部分读取配置，即 Claude Code 当前使用的相同配置：

- `ANTHROPIC_BASE_URL`：API 端点 URL
- `ANTHROPIC_API_KEY`：API 密钥
- `ANTHROPIC_MODEL`：模型名称

API 格式从 URL 自动检测：
- 含 `/compatible-mode` → OpenAI 格式
- 其余 → Anthropic 格式

**settings.json 示例：**
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "ANTHROPIC_API_KEY": "YOUR_API_KEY_HERE",
    "ANTHROPIC_MODEL": "qwen-vl-plus"
  }
}
```

请使用支持视觉的模型（如 `qwen-vl-plus`、`gpt-4o`）进行图片分析。

## 支持的图片格式

PNG、JPG、JPEG、GIF、BMP、WebP、SVG、TIFF、ICO、AVIF、HEIC/HEIF

视觉 API 不直接支持的格式（SVG、BMP、TIFF 等）会在发送前自动转换为 PNG。如果所需的转换库（cairosvg 或 Pillow）不可用，这些格式会回退到 Claude 内置的图片处理。