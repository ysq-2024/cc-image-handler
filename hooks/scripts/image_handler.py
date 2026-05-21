#!/usr/bin/env python3
"""Multimodal image handler for Claude Code hooks.

Intercepts image inputs and sends them to a configured multimodal model
for analysis. The model's description replaces the raw image in Claude's
context (complete replacement mode).

Uses the official openai / anthropic Python SDKs for API calls.

Config is read from ~/.claude/settings.json env section:
  - ANTHROPIC_BASE_URL: API endpoint URL
  - ANTHROPIC_API_KEY: API key
  - ANTHROPIC_MODEL: model name

Format is auto-detected from URL (/compatible-mode → openai, else → anthropic).

Supported API formats:
  - "openai": Uses the openai SDK (works with DashScope, vLLM, Ollama, etc.)
  - "anthropic": Uses the anthropic SDK (native Claude format)

Usage:
  python3 image_handler.py --event pre_read       # PreToolUse on Read
  python3 image_handler.py --event user_prompt    # UserPromptSubmit
  python3 image_handler.py --event post_bash      # PostToolUse on Bash
"""

import sys
import json
import base64
import os
import re
import mimetypes
import argparse
import tempfile

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import cairosvg
except ImportError:
    cairosvg = None

try:
    from PIL import Image
except ImportError:
    Image = None


def _check_cairosvg():
    """Verify cairosvg can actually convert (requires Cairo system library)."""
    if cairosvg is None:
        return False
    try:
        cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
        return True
    except Exception:
        return False


def _check_pillow():
    """Verify Pillow can actually convert images."""
    if Image is None:
        return False
    try:
        img = Image.new("RGB", (1, 1))
        img.save(tempfile.NamedTemporaryFile(suffix=".png", delete=False).name, format="PNG")
        return True
    except Exception:
        return False


CAIROSVG_AVAILABLE = _check_cairosvg()
PILLOW_AVAILABLE = _check_pillow()

# Dynamically build supported extensions based on available converters.
DIRECTLY_SUPPORTED = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
CAIROSVG_FORMATS = {".svg"} if CAIROSVG_AVAILABLE else set()
PILLOW_FORMATS = {".bmp", ".tiff", ".tif", ".ico", ".avif", ".heic", ".heif"} if PILLOW_AVAILABLE else set()

IMAGE_EXTENSIONS = frozenset(DIRECTLY_SUPPORTED | CAIROSVG_FORMATS | PILLOW_FORMATS)

if not CAIROSVG_AVAILABLE:
    sys.stderr.write("image_handler: cairosvg/Cairo unavailable — SVG conversion disabled\n")
if not PILLOW_AVAILABLE:
    sys.stderr.write("image_handler: Pillow unavailable — BMP/TIFF/ICO/AVIF/HEIC conversion disabled\n")

SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")


def load_config():
    """Read url, apiKey, model from ~/.claude/settings.json env section.

    Env keys: ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL
    Format is auto-detected from URL pattern.
    """
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
    except FileNotFoundError:
        sys.stderr.write("image_handler: ~/.claude/settings.json not found\n")
        return None
    except Exception as e:
        sys.stderr.write(f"image_handler: settings read error: {e}\n")
        return None

    env = settings.get("env", {})
    url = env.get("ANTHROPIC_BASE_URL", "")
    api_key = env.get("ANTHROPIC_API_KEY", "")
    model = env.get("ANTHROPIC_MODEL", "")

    if not url or not api_key:
        sys.stderr.write("image_handler: missing ANTHROPIC_BASE_URL or ANTHROPIC_API_KEY in settings.json\n")
        return None

    format = detect_format(url)

    return {
        "url": url,
        "apiKey": api_key,
        "model": model,
        "format": format,
        "prompt": DEFAULT_PROMPT,
        "timeout": 60,
    }

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".heif": "image/heif",
}

DEFAULT_PROMPT = (
    "Please describe this image in detail, including any text, diagrams, "
    "code, UI elements, charts, or visual information you can see. "
    "Be thorough and precise. If there is text in the image, transcribe it exactly."
)

MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB


def detect_format(url):
    """Auto-detect API format from the URL pattern.

    /compatible-mode or /v1/chat/completions → openai
    Everything else (e.g. /apps/anthropic) → anthropic
    """
    if "/compatible-mode" in url or "/v1/chat/completions" in url:
        return "openai"
    return "anthropic"


SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")


def is_image_file(path):
    ext = os.path.splitext(path)[1].lower()
    return ext in IMAGE_EXTENSIONS


def get_media_type(path):
    ext = os.path.splitext(path)[1].lower()
    return MEDIA_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "image/png"


def encode_image(path):
    size = os.path.getsize(path)
    if size > MAX_IMAGE_SIZE:
        raise ValueError(f"Image too large ({size} bytes, max {MAX_IMAGE_SIZE})")
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# Formats that most vision APIs accept directly.
DIRECTLY_SUPPORTED = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def prepare_image_for_api(image_path):
    """Convert image to a format the multimodal API can accept.

    Returns (prepared_path, media_type, is_temp).
    - prepared_path: file path to send (may be a temp PNG for SVGs etc.)
    - media_type: MIME type of the prepared file
    - is_temp: True if prepared_path is a temp file that should be cleaned up
    """
    ext = os.path.splitext(image_path)[1].lower()

    if ext in DIRECTLY_SUPPORTED:
        return image_path, get_media_type(image_path), False

    # SVG → PNG via cairosvg
    if ext == ".svg":
        if not CAIROSVG_AVAILABLE:
            raise RuntimeError("SVG conversion unavailable: cairosvg/Cairo library not installed")
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            cairosvg.svg2png(url=image_path, write_to=tmp.name)
            tmp.close()
            return tmp.name, "image/png", True
        except Exception:
            tmp.close()
            os.unlink(tmp.name)
            raise

    # BMP, TIFF, ICO, AVIF, HEIC → PNG via Pillow
    if ext in PILLOW_FORMATS:
        if not PILLOW_AVAILABLE:
            raise RuntimeError(f"{ext} conversion unavailable: Pillow not installed")
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            img = Image.open(image_path)
            img.save(tmp.name, format="PNG")
            tmp.close()
            return tmp.name, "image/png", True
        except Exception:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise

    # No conversion available — try sending original format anyway
    return image_path, get_media_type(image_path), False


def normalize_openai_base_url(url):
    """Strip /chat/completions suffix so the openai SDK can use it as base_url.

    The SDK appends /chat/completions automatically, so if the user provides
    the full endpoint URL we need to trim it down to the base.
    """
    url = url.rstrip("/")
    for suffix in ("/chat/completions", "/v1/chat/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    # Ensure trailing slash — the openai SDK expects base_url to end with /
    if not url.endswith("/"):
        url += "/"
    return url


def call_multimodal_api_openai(config, image_path):
    """Call multimodal model using the openai SDK."""
    if OpenAI is None:
        raise ImportError("openai package not installed. Run: pip3 install openai")

    prepared_path, media_type, is_temp = prepare_image_for_api(image_path)
    image_b64 = encode_image(prepared_path)
    if is_temp:
        try:
            os.unlink(prepared_path)
        except OSError:
            pass
    prompt_text = config.get("prompt", DEFAULT_PROMPT)
    model_name = config.get("model", "gpt-4o")
    timeout = config.get("timeout", 60)
    base_url = normalize_openai_base_url(config["url"])

    client = OpenAI(
        api_key=config["apiKey"],
        base_url=base_url,
        timeout=timeout,
    )

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type};base64,{image_b64}",
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
        max_tokens=4096,
    )

    choice = response.choices[0]
    content = choice.message.content
    if content:
        return content
    return "[No response from model]"


def call_multimodal_api_anthropic(config, image_path):
    """Call multimodal model using the anthropic SDK."""
    if anthropic is None:
        raise ImportError("anthropic package not installed. Run: pip3 install anthropic")

    prepared_path, media_type, is_temp = prepare_image_for_api(image_path)
    image_b64 = encode_image(prepared_path)
    if is_temp:
        try:
            os.unlink(prepared_path)
        except OSError:
            pass
    prompt_text = config.get("prompt", DEFAULT_PROMPT)
    model_name = config.get("model", "claude-sonnet-4-6")
    timeout = config.get("timeout", 60)

    client = anthropic.Anthropic(
        api_key=config["apiKey"],
        base_url=config["url"].rstrip("/"),
        timeout=timeout,
    )

    message = client.messages.create(
        model=model_name,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
    )

    text_blocks = [b.text for b in message.content if b.type == "text"]
    return "\n".join(text_blocks) if text_blocks else "[No text response from model]"


def call_multimodal_api(config, image_path):
    """Send an image to the multimodal model and return its description."""
    api_format = config.get("format", "openai")

    if api_format == "anthropic":
        return call_multimodal_api_anthropic(config, image_path)
    else:
        return call_multimodal_api_openai(config, image_path)


def find_image_paths_in_text(text):
    """Extract image file paths from text using regex.

    Detects all known image formats regardless of converter availability.
    Filtering by actual support is done later by is_image_file().
    """
    exts = "png|jpg|jpeg|gif|bmp|webp|svg|tiff|tif|ico|avif|heic|heif"
    abs_pattern = r'(?:[/~][\w/\-\.]+\.(?:' + exts + r'))'
    rel_pattern = r'(?:\.{1,2}/[\w/\-\.]+\.(?:' + exts + r'))'
    paths = re.findall(abs_pattern, text, re.IGNORECASE)
    paths.extend(re.findall(rel_pattern, text, re.IGNORECASE))
    return list(dict.fromkeys(paths))


def resolve_path(p):
    """Expand ~ and resolve relative paths to absolute."""
    expanded = os.path.expanduser(p)
    if not os.path.isabs(expanded):
        expanded = os.path.abspath(expanded)
    return expanded


def handle_pre_read(hook_input):
    """PreToolUse on Read: intercept image file reads, replace with multimodal description."""
    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    if not file_path or not is_image_file(file_path):
        return {"continue": True}

    config = load_config()
    if not config:
        return {"continue": True}

    resolved = resolve_path(file_path)
    if not os.path.isfile(resolved):
        return {"continue": True}

    try:
        description = call_multimodal_api(config, resolved)
        model_name = config.get("model", "")
        return {
            "continue": False,
            "stopReason": f"Image {resolved} analyzed by multimodal model {model_name}",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    f"[Multimodal Model ({model_name}) Analysis of {resolved}]\n"
                    f"{description}\n\n"
                    f"---\n"
                    f"Note: This image was analyzed by an external multimodal model. "
                    f"The raw image data was NOT passed to Claude — only this text description is available."
                ),
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Image processed by multimodal model ({model_name}) instead of Claude's built-in Read"
                ),
            },
        }
    except Exception as e:
        sys.stderr.write(f"multimodal_handler: API call failed: {e}\n")
        return {
            "continue": True,
            "systemMessage": (
                f"Multimodal model call failed ({type(e).__name__}: {e}). "
                f"Falling back to Claude's built-in image processing."
            ),
        }


def handle_user_prompt(hook_input):
    """UserPromptSubmit: detect image file references in prompt text."""
    prompt_text = ""
    for key in ("prompt", "message", "content", "user_message", "text"):
        val = hook_input.get(key, "")
        if val:
            if isinstance(val, (dict, list)):
                prompt_text = json.dumps(val)
            else:
                prompt_text = str(val)
            break

    if not prompt_text:
        return {"continue": True}

    config = load_config()
    if not config:
        return {"continue": True}

    paths = find_image_paths_in_text(prompt_text)
    descriptions = []
    for p in paths:
        resolved = resolve_path(p)
        if os.path.isfile(resolved) and is_image_file(resolved):
            try:
                desc = call_multimodal_api(config, resolved)
                descriptions.append(f"[{resolved}]\n{desc}")
            except Exception as e:
                descriptions.append(f"[{resolved}]\nAnalysis failed: {type(e).__name__}: {e}")

    if descriptions:
        combined = "\n\n---\n\n".join(descriptions)
        model_name = config.get("model", "")
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    f"[Multimodal Model ({model_name}) Analysis of referenced images]\n"
                    f"{combined}\n\n"
                    f"---\n"
                    f"Note: These images were analyzed by an external multimodal model. "
                    f"Only text descriptions are available, not raw image data."
                ),
            },
        }

    return {"continue": True}


def handle_post_bash(hook_input):
    """PostToolUse on Bash: detect image files produced by commands."""
    config = load_config()
    if not config:
        return {"continue": True}

    tool_response = hook_input.get("tool_response", {})
    output = ""
    if isinstance(tool_response, dict):
        output = tool_response.get("stdout", "") or tool_response.get("output", "") or ""
    elif isinstance(tool_response, str):
        output = tool_response

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""

    combined_text = f"{command}\n{output}"

    paths = find_image_paths_in_text(combined_text)
    descriptions = []
    for p in paths:
        resolved = resolve_path(p)
        if os.path.isfile(resolved) and is_image_file(resolved):
            try:
                desc = call_multimodal_api(config, resolved)
                descriptions.append(f"[{resolved}]\n{desc}")
            except Exception as e:
                descriptions.append(f"[{resolved}]\nAnalysis failed: {type(e).__name__}: {e}")

    if descriptions:
        combined = "\n\n---\n\n".join(descriptions)
        model_name = config.get("model", "")
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": (
                    f"[Multimodal Model ({model_name}) Analysis of images produced by command]\n"
                    f"{combined}\n\n"
                    f"---\n"
                    f"Note: These images were analyzed by an external multimodal model. "
                    f"Only text descriptions are available, not raw image data."
                ),
            },
        }

    return {"continue": True}


def main():
    parser = argparse.ArgumentParser(description="Multimodal image handler for Claude Code hooks")
    parser.add_argument(
        "--event",
        required=True,
        choices=["pre_read", "user_prompt", "post_bash"],
        help="Hook event type",
    )
    args = parser.parse_args()

    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        print(json.dumps({"continue": True}))
        return
    except Exception:
        print(json.dumps({"continue": True}))
        return

    handlers = {
        "pre_read": handle_pre_read,
        "user_prompt": handle_user_prompt,
        "post_bash": handle_post_bash,
    }

    handler = handlers.get(args.event)
    if not handler:
        print(json.dumps({"continue": True}))
        return

    result = handler(hook_input)
    print(json.dumps(result))


if __name__ == "__main__":
    main()