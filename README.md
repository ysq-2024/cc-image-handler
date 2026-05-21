# multimodal-image-handler

Claude Code plugin that intercepts image inputs and sends them to an external multimodal model for analysis, replacing Claude's built-in image processing with text descriptions from the configured model.

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

The plugin **defaults to Claude Code's current model configuration** (the same URL, API key, and model Claude Code is already using). No config file is needed if you want to use the same provider.

Config resolution priority (high → low):
1. `~/.claude/multimodal-config.json` — explicit overrides (any field)
2. Claude Code env vars — `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`
3. Auto-detection — API format inferred from URL pattern

**Minimal config** (only override what differs, e.g. use a vision-specific model):
```json
{
  "model": "qwen-vl-plus"
}
```

**Full config** (override everything):
```json
{
  "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
  "apiKey": "YOUR_API_KEY_HERE",
  "model": "qwen-vl-plus",
  "format": "openai",
  "prompt": "Please describe this image in detail...",
  "timeout": 60
}
```

**Fields:**
- `url`: API endpoint. Falls back to `ANTHROPIC_BASE_URL`. For OpenAI-compatible providers, the handler strips `/chat/completions` automatically.
- `apiKey`: API key. Falls back to `ANTHROPIC_AUTH_TOKEN`.
- `model`: Model name. Falls back to `ANTHROPIC_MODEL`. Use a vision-capable model (e.g. `qwen-vl-plus`, `gpt-4o`).
- `format`: `"openai"` or `"anthropic"`. Auto-detected from URL: `/compatible-mode` → openai, else → anthropic.
- `prompt`: Custom prompt sent to the model with each image.
- `timeout`: Request timeout in seconds (default: 60).

See `hooks/scripts/multimodal-config.example.json` for a minimal template.

## Dependencies

```bash
pip3 install openai anthropic cairosvg Pillow
```

- `openai`: Required for `"format": "openai"`
- `anthropic`: Required for `"format": "anthropic"`
- `cairosvg`: Optional, enables SVG-to-PNG conversion
- `Pillow`: Optional, enables BMP/TIFF/ICO/AVIF/HEIC conversion to PNG

## Supported image formats

PNG, JPG, JPEG, GIF, BMP, WebP, SVG, TIFF, ICO, AVIF, HEIC/HEIF

Formats not directly supported by the vision API (SVG, BMP, TIFF, etc.) are automatically converted to PNG before sending.