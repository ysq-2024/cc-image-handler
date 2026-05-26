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

Two hooks intercept image inputs at different points:

| Hook | Event | What it catches |
|------|-------|-----------------|
| PreToolUse/Read | Claude reads an image file | Blocks Read, injects multimodal description |
| UserPromptSubmit | User references image paths in prompt | Detects paths, adds analysis as context |

When an image is intercepted, the handler:
1. Converts unsupported formats (SVG via cairosvg, BMP/TIFF via Pillow) to PNG
2. Base64-encodes the image
3. Calls the configured multimodal API using the openai or anthropic SDK
4. Injects the model's text description into Claude's context

If the API call fails, Claude falls back to its built-in image processing.

## Configuration

The plugin reads config from `~/.claude/settings.json` env section. Base keys are the same values Claude Code is already using. Override keys allow specifying a different model/endpoint specifically for image analysis.

| Key | Purpose | Required |
|-----|---------|----------|
| `ANTHROPIC_BASE_URL` | API endpoint URL (base) | Yes |
| `ANTHROPIC_API_KEY` | API key (base) | Yes |
| `ANTHROPIC_MODEL` | Model name (base) | Yes |
| `MULTIMODAL_BASE_URL` | Override URL for image analysis | No |
| `MULTIMODAL_API_KEY` | Override API key for image analysis | No |
| `MULTIMODAL_MODEL` | Override model for image analysis | No |
| `MULTIMODAL_FORMAT` | Override API format: `"anthropic"` or `"openai"` | No |

Override keys take priority over base keys. If your Claude Code model doesn't support vision, set `MULTIMODAL_*` to a vision-capable model.

API format defaults to `"anthropic"`. Auto-detect switches to `"openai"` if URL contains `/compatible-mode`. Set `MULTIMODAL_FORMAT` to override.

**Minimal settings.json (same model for Claude Code and image analysis):**
```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/apps/anthropic",
    "ANTHROPIC_API_KEY": "YOUR_API_KEY_HERE",
    "ANTHROPIC_MODEL": "qwen-vl-plus"
  }
}
```

**With multimodal override (Claude Code uses non-vision model, image analysis uses vision model):**
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

Use a vision-capable model (e.g. `qwen-vl-plus`, `gpt-4o`) for image analysis. Images are always sent as base64.

## Supported image formats

PNG, JPG, JPEG, GIF, BMP, WebP, SVG, TIFF, ICO, AVIF, HEIC/HEIF

Formats not directly supported by the vision API (SVG, BMP, TIFF, etc.) are automatically converted to PNG before sending. If the required converter (cairosvg or Pillow) is unavailable, those formats fall back to Claude's built-in image processing.

## Cross-platform support

The plugin runs on Linux, macOS, and Windows with automatic platform detection:

- **Path handling**: All file paths are normalized via `os.path.normpath()`, collapsing `..` and `.` components to prevent cache misses (e.g. `/mnt/workspace/../test.jpg` → `/mnt/test.jpg`)
- **Cache key normalization**: On Windows (case-insensitive filesystem), cache keys are lowercased so `C:\Users\Test\photo.png` and `c:\users\test\photo.png` share one entry. On POSIX, case is preserved.
- **Path detection in prompts**: Both `/` and `\` separators are recognized. Windows drive-letter paths (`C:\...`, `D:/...`) are matched on Windows; POSIX paths (`/home/...`, `~/...`) are matched on Linux/macOS.
- **Signal handling**: SIGTERM handler is registered only where available (POSIX); skipped on Windows to avoid crashes.
- **Environment variables**: On Windows, the `CD` env var is checked as a fallback when `PWD` is absent.
- **Cairo installation** (for SVG support):
  - Ubuntu/Debian: `sudo apt install libcairo2-dev`
  - macOS: `brew install cairo`
  - Alpine: `apk add cairo-dev`
  - Windows: `pip install cairosvg` plus installing the GTK runtime (see [cairosvg docs](https://cairosvg.org/))