# cc-image-handler

[中文文档](README_zh.md)

Claude Code plugin that intercepts image inputs and sends them to an external multimodal model for analysis, replacing Claude's built-in image processing with text descriptions from the configured model.

## Installation

### Option 1: CLI command (recommended)

Add the marketplace source and install the plugin:

```bash
claude plugin marketplace add ysq-2024/cc-image-handler
claude plugin install cc-image-handler@cc-image-handler
```

Or install with a specific scope:

```bash
claude plugin install cc-image-handler@cc-image-handler --scope user   # global (default)
claude plugin install cc-image-handler@cc-image-handler --scope project  # project-level
```

### Option 2: Manual setup

Add the marketplace source and enable the plugin in `~/.claude/settings.json`:

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

Then reload plugins in Claude Code: `/reload-plugins`

### Option 3: Local directory (for development)

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

### Option 4: One-off test

```bash
claude --plugin-dir /path/to/cc-image-handler
```

## Prerequisites

```bash
pip3 install openai anthropic cairosvg Pillow
```

- `openai`: Required for `"format": "openai"`
- `anthropic`: Required for `"format": "anthropic"`
- `cairosvg`: Optional, enables SVG-to-PNG conversion. **Also requires the Cairo system library** — install it first:
  - Ubuntu/Debian: `sudo apt install libcairo2-dev`
  - macOS: `brew install cairo`
  - Alpine: `apk add cairo-dev`
- `Pillow`: Optional, enables BMP/TIFF/ICO/AVIF/HEIC conversion to PNG

## How it works

Three hooks intercept image inputs at different points:

| Hook | Event | What it catches |
|------|-------|-----------------|
| PreToolUse/Read | Claude reads an image file | Blocks Read, injects multimodal description |
| UserPromptSubmit | User references image paths in prompt | Detects paths, adds analysis as context |
| PostToolUse/Bash | Bash command produces image files | Scans output for paths, adds analysis |

When an image is intercepted, the handler:
1. Converts unsupported formats (SVG via cairosvg, BMP/TIFF via Pillow) to PNG
2. Base64-encodes the image
3. Calls the configured multimodal API using the openai or anthropic SDK
4. Injects the model's text description into Claude's context

If the API call fails, Claude falls back to its built-in image processing.

## Configuration

The plugin reads config from `~/.claude/settings.json` env section — the same values Claude Code is already using:

- `ANTHROPIC_BASE_URL`: API endpoint URL
- `ANTHROPIC_API_KEY`: API key
- `ANTHROPIC_MODEL`: model name

API format is auto-detected from the URL:
- `/compatible-mode` → OpenAI format
- Otherwise → Anthropic format

**Example settings.json:**
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "ANTHROPIC_API_KEY": "YOUR_API_KEY_HERE",
    "ANTHROPIC_MODEL": "qwen-vl-plus"
  }
}
```

Use a vision-capable model (e.g. `qwen-vl-plus`, `gpt-4o`) for image analysis.

## Supported image formats

PNG, JPG, JPEG, GIF, BMP, WebP, SVG, TIFF, ICO, AVIF, HEIC/HEIF

Formats not directly supported by the vision API (SVG, BMP, TIFF, etc.) are automatically converted to PNG before sending. If the required converter (cairosvg or Pillow) is unavailable, those formats fall back to Claude's built-in image processing.