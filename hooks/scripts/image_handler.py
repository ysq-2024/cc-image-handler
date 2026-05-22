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
import logging

LOG_PATH = os.path.expanduser("~/.claude/image_handler.log")


def log(msg):
    """Write to log file and stderr for debug."""
    try:
        with open(LOG_PATH, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    sys.stderr.write(msg + "\n")


logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("image_handler")

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
    logger.warning("cairosvg/Cairo unavailable — SVG conversion disabled")
if not PILLOW_AVAILABLE:
    logger.warning("Pillow unavailable — BMP/TIFF/ICO/AVIF/HEIC conversion disabled")

SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")


def load_config():
    """Read config from ~/.claude/settings.json env section.

    Base keys: ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL
    Override keys for image analysis: MULTIMODAL_BASE_URL, MULTIMODAL_API_KEY, MULTIMODAL_MODEL, MULTIMODAL_FORMAT
    Override keys take priority over base keys when set.
    Format defaults to anthropic, auto-detected from URL if MULTIMODAL_FORMAT not set.
    """
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
    except FileNotFoundError:
        logger.error("~/.claude/settings.json not found")
        return None
    except Exception as e:
        logger.error("settings read error: %s", e)
        return None

    env = settings.get("env", {})
    url = env.get("MULTIMODAL_BASE_URL", "") or env.get("ANTHROPIC_BASE_URL", "")
    api_key = env.get("MULTIMODAL_API_KEY", "") or env.get("ANTHROPIC_API_KEY", "")
    model = env.get("MULTIMODAL_MODEL", "") or env.get("ANTHROPIC_MODEL", "")

    if not url or not api_key:
        logger.error("missing ANTHROPIC_BASE_URL or ANTHROPIC_API_KEY in settings.json")
        return None

    # Format: explicit override > auto-detect > default anthropic
    format = env.get("MULTIMODAL_FORMAT", "")
    if not format:
        format = "anthropic"
        if "/compatible-mode" in url or "/v1/chat/completions" in url:
            format = "openai"

    logger.info("config: url=%s, model=%s, format=%s", url, model, format)

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
        max_tokens=40960,
        stream=True,
    )

    chunks = []
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            chunks.append(chunk.choices[0].delta.content)
    return "".join(chunks) or "[No response from model]"


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

    with client.messages.stream(
        model=model_name,
        max_tokens=40960,
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
    ) as stream:
        text = stream.get_final_text()
    return text or "[No text response from model]"


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
    Supports: absolute (/...), home (~...), relative (../...), and @-references (@file.ext)
    """
    exts = "png|jpg|jpeg|gif|bmp|webp|svg|tiff|tif|ico|avif|heic|heif"
    abs_pattern = r'(?:[/~][\w/\-\.]+\.(?:' + exts + r'))'
    rel_pattern = r'(?:\.{1,2}/[\w/\-\.]+\.(?:' + exts + r'))'
    ref_pattern = r'(?:@[\w/\-\.]+\.(?:' + exts + r'))'
    paths = re.findall(abs_pattern, text, re.IGNORECASE)
    paths.extend(re.findall(rel_pattern, text, re.IGNORECASE))
    refs = re.findall(ref_pattern, text, re.IGNORECASE)
    # Strip leading @ from @-references for file resolution
    paths.extend(r[1:] for r in refs)
    return list(dict.fromkeys(paths))


def _get_search_dirs():
    """Get directories to search for bare filenames.

    Uses PWD env var (set by Claude Code to the user's project dir),
    then CWD, then home directory.
    """
    dirs = []
    pwd = os.environ.get("PWD", "")
    if pwd:
        dirs.append(pwd)
    cwd = os.getcwd()
    if cwd != pwd:
        dirs.append(cwd)
    dirs.append(os.path.expanduser("~"))
    return dirs


def resolve_path(p, search_dirs=None):
    """Expand ~ and resolve relative/bare filenames to absolute paths.

    For filenames without a path prefix, search in likely directories
    since the hook script's CWD may differ from the user's project dir.
    search_dirs: list of directories to try. Defaults to _get_search_dirs().
    """
    expanded = os.path.expanduser(p)
    if os.path.isabs(expanded):
        return expanded

    # Collect candidate directories for bare/relative filenames
    dirs = list(search_dirs or _get_search_dirs())

    for d in dirs:
        candidate = os.path.join(d, expanded)
        if os.path.isfile(candidate):
            return candidate

    # Fallback: resolve relative to first search dir (may not exist)
    return os.path.join(dirs[0], expanded) if dirs else os.path.abspath(expanded)


def handle_pre_read(hook_input):
    """PreToolUse on Read: intercept image file reads, replace with multimodal description."""
    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    logger.info("pre_read: file_path=%s", file_path)

    if not file_path or not is_image_file(file_path):
        logger.debug("pre_read: not an image, continuing")
        return {"continue": True}

    config = load_config()
    if not config:
        logger.warning("pre_read: no config, continuing")
        return {"continue": True}

    resolved = resolve_path(file_path)
    if not os.path.isfile(resolved):
        logger.warning("pre_read: file not found: %s", resolved)
        return {"continue": True}

    logger.info("pre_read: analyzing %s with model %s", resolved, config.get("model", ""))
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
        logger.error("API call failed: %s", e)
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

    logger.info("user_prompt: text=%s", prompt_text[:200])

    if not prompt_text:
        logger.debug("user_prompt: no text, continuing")
        return {"continue": True}

    config = load_config()
    if not config:
        logger.warning("user_prompt: no config, continuing")
        return {"continue": True}

    paths = find_image_paths_in_text(prompt_text)
    logger.info("user_prompt: detected paths=%s", paths)
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
        logger.warning("post_bash: no config, continuing")
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
    logger.info("post_bash: combined_text=%s", combined_text[:200])

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