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
  - MULTIMODAL_BASE_URL/MULTIMODAL_API_KEY/MULTIMODAL_MODEL/MULTIMODAL_FORMAT: overrides

Format is auto-detected from URL (/compatible-mode → openai, else → anthropic).

Supported API formats:
  - "openai": Uses the openai SDK (works with DashScope, vLLM, Ollama, etc.)
  - "anthropic": Uses the anthropic SDK (native Claude format)

Usage:
  python3 image_handler.py --event pre_read       # PreToolUse on Read
  python3 image_handler.py --event user_prompt    # UserPromptSubmit
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
import signal
import time

LOG_PATH = os.path.expanduser("~/.claude/image_handler.log")
CACHE_PATH = os.path.expanduser("~/.claude/image_handler_cache.json")
SETTINGS_PATH = os.path.expanduser("~/.claude/settings.json")


logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("image_handler")


def _signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.critical("Process killed by signal %s (PID=%d)", sig_name, os.getpid())
    sys.exit(128 + signum)


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None
    logger.warning("openai package not installed")

try:
    import anthropic
except ImportError:
    anthropic = None
    logger.warning("anthropic package not installed")

try:
    import cairosvg
except ImportError as e:
    cairosvg = None
    logger.warning("cairosvg not installed: %s", e)

try:
    from PIL import Image
except ImportError as e:
    Image = None
    logger.warning("Pillow not installed: %s", e)


def _check_cairosvg():
    if cairosvg is None:
        return False
    try:
        cairosvg.svg2png(bytestring=b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>')
        return True
    except Exception as e:
        logger.warning("cairosvg check failed: %s: %s", type(e).__name__, e)
        return False


def _check_pillow():
    if Image is None:
        return False
    try:
        img = Image.new("RGB", (1, 1))
        img.save(tempfile.NamedTemporaryFile(suffix=".png", delete=False).name, format="PNG")
        return True
    except Exception as e:
        logger.warning("Pillow check failed: %s: %s", type(e).__name__, e)
        return False


CAIROSVG_AVAILABLE = _check_cairosvg()
PILLOW_AVAILABLE = _check_pillow()

DIRECTLY_SUPPORTED = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
CAIROSVG_FORMATS = {".svg"} if CAIROSVG_AVAILABLE else set()
PILLOW_FORMATS = {".bmp", ".tiff", ".tif", ".ico", ".avif", ".heic", ".heif"} if PILLOW_AVAILABLE else set()

IMAGE_EXTENSIONS = frozenset(DIRECTLY_SUPPORTED | CAIROSVG_FORMATS | PILLOW_FORMATS)

logger.info("env check: CAIROSVG_AVAILABLE=%s, PILLOW_AVAILABLE=%s, IMAGE_EXTENSIONS=%s",
            CAIROSVG_AVAILABLE, PILLOW_AVAILABLE, sorted(IMAGE_EXTENSIONS))

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


def load_config():
    """Read config from ~/.claude/settings.json env section."""
    try:
        with open(SETTINGS_PATH) as f:
            settings = json.load(f)
    except FileNotFoundError:
        logger.error("~/.claude/settings.json not found")
        return None
    except json.JSONDecodeError as e:
        logger.error("settings.json JSON decode error: %s", e)
        return None
    except Exception as e:
        logger.error("settings read error: %s: %s", type(e).__name__, e)
        return None

    env = settings.get("env", {})
    url = env.get("MULTIMODAL_BASE_URL", "") or env.get("ANTHROPIC_BASE_URL", "")
    api_key = env.get("MULTIMODAL_API_KEY", "") or env.get("ANTHROPIC_API_KEY", "")
    model = env.get("MULTIMODAL_MODEL", "") or env.get("ANTHROPIC_MODEL", "")

    if not url or not api_key:
        logger.error("missing ANTHROPIC_BASE_URL or ANTHROPIC_API_KEY in settings.json env (url=%s, apiKey=%s)",
                     url[:50] if url else "", api_key[:10] if api_key else "")
        return None

    format = env.get("MULTIMODAL_FORMAT", "") or "anthropic"

    logger.info("config: url=%s, model=%s, format=%s", url, model, format)

    return {
        "url": url,
        "apiKey": api_key,
        "model": model,
        "format": format,
        "prompt": DEFAULT_PROMPT,
        "timeout": 60,
    }


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


def prepare_image_for_api(image_path):
    """Convert image to a format the multimodal API can accept."""
    ext = os.path.splitext(image_path)[1].lower()
    logger.info("prepare_image: path=%s, ext=%s, size=%d", image_path, ext, os.path.getsize(image_path))

    if ext in DIRECTLY_SUPPORTED:
        return image_path, get_media_type(image_path), False

    if ext == ".svg":
        if not CAIROSVG_AVAILABLE:
            raise RuntimeError("SVG conversion unavailable: cairosvg/Cairo library not installed")
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            cairosvg.svg2png(url=image_path, write_to=tmp.name)
            tmp.close()
            logger.info("SVG→PNG conversion success: %s", tmp.name)
            return tmp.name, "image/png", True
        except Exception as e:
            tmp.close()
            os.unlink(tmp.name)
            logger.error("SVG→PNG conversion failed: %s: %s", type(e).__name__, e)
            raise

    if ext in PILLOW_FORMATS:
        if not PILLOW_AVAILABLE:
            raise RuntimeError(f"{ext} conversion unavailable: Pillow not installed")
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            img = Image.open(image_path)
            img.save(tmp.name, format="PNG")
            tmp.close()
            logger.info("%s→PNG conversion success: %s", ext, tmp.name)
            return tmp.name, "image/png", True
        except Exception as e:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            logger.error("%s→PNG conversion failed: %s: %s", ext, type(e).__name__, e)
            raise

    logger.warning("no converter for %s, sending original format", ext)
    return image_path, get_media_type(image_path), False


def normalize_openai_base_url(url):
    url = url.rstrip("/")
    for suffix in ("/chat/completions", "/v1/chat/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    if not url.endswith("/"):
        url += "/"
    logger.info("normalize_openai_base_url: %s", url)
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

    logger.info("openai call: model=%s, base_url=%s, media_type=%s, b64_len=%d, timeout=%d",
                model_name, base_url, media_type, len(image_b64), timeout)

    client = OpenAI(
        api_key=config["apiKey"],
        base_url=base_url,
        timeout=timeout,
    )

    try:
        t0 = time.time()
        logger.info("openai: sending request...")
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
        logger.info("openai: response stream started, elapsed=%.1fs", time.time() - t0)

        chunks = []
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content:
                chunks.append(chunk.choices[0].delta.content)
        logger.info("openai: stream finished, elapsed=%.1fs", time.time() - t0)
        result = "".join(chunks) or "[No response from model]"
        logger.info("openai response len=%d, first 200=%s", len(result), result[:200])
        return result
    except Exception as e:
        logger.error("openai API call failed after %.1fs: %s: %s\nFull traceback:",
                     time.time() - t0, type(e).__name__, e, exc_info=True)
        raise


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

    logger.info("anthropic call: model=%s, base_url=%s, media_type=%s, b64_len=%d",
                model_name, config["url"].rstrip("/"), media_type, len(image_b64))

    client = anthropic.Anthropic(
        api_key=config["apiKey"],
        base_url=config["url"].rstrip("/"),
        timeout=timeout,
    )

    try:
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
        result = text or "[No text response from model]"
        logger.info("anthropic response len=%d, first 200=%s", len(result), result[:200])
        return result
    except Exception as e:
        logger.error("anthropic API call failed: %s: %s\nFull traceback:", type(e).__name__, e, exc_info=True)
        raise


def call_multimodal_api(config, image_path):
    api_format = config.get("format", "openai")
    logger.info("call_multimodal_api: format=%s, image=%s", api_format, image_path)
    if api_format == "anthropic":
        return call_multimodal_api_anthropic(config, image_path)
    else:
        return call_multimodal_api_openai(config, image_path)


def find_image_paths_in_text(text):
    exts = "png|jpg|jpeg|gif|bmp|webp|svg|tiff|tif|ico|avif|heic|heif"
    abs_pattern = r'(?:[/~][\w/\-\.]+\.(?:' + exts + r'))'
    rel_pattern = r'(?:\.{1,2}/[\w/\-\.]+\.(?:' + exts + r'))'
    ref_pattern = r'(?:@[\w/\-\.]+\.(?:' + exts + r'))'
    paths = re.findall(abs_pattern, text, re.IGNORECASE)
    paths.extend(re.findall(rel_pattern, text, re.IGNORECASE))
    refs = re.findall(ref_pattern, text, re.IGNORECASE)
    paths.extend(r[1:] for r in refs)
    return list(dict.fromkeys(paths))


def _get_search_dirs():
    dirs = []
    pwd = os.environ.get("PWD", "")
    if pwd:
        dirs.append(pwd)
    cwd = os.getcwd()
    if cwd != pwd:
        dirs.append(cwd)
    dirs.append(os.path.expanduser("~"))
    logger.debug("search_dirs=%s", dirs)
    return dirs


def _load_cache():
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        logger.warning("cache JSON decode error: %s", e)
        return {}


def _save_cache(cache):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f)
    except Exception as e:
        logger.warning("failed to save cache: %s: %s", type(e).__name__, e)


def _get_file_mtime(path):
    try:
        return str(os.path.getmtime(path))
    except OSError as e:
        logger.warning("getmtime failed for %s: %s", path, e)
        return ""


def _check_cache(cache, path):
    entry = cache.get(path)
    if not entry:
        logger.debug("cache miss: %s", path)
        return None
    if _get_file_mtime(path) != entry.get("mtime", ""):
        logger.info("cache invalidated: %s (mtime changed)", path)
        return None
    logger.info("cache hit: %s", path)
    return entry.get("description")


def _update_cache(cache, path, description):
    cache[path] = {
        "mtime": _get_file_mtime(path),
        "description": description,
    }
    _save_cache(cache)
    logger.debug("cache updated: %s", path)


def resolve_path(p, search_dirs=None):
    expanded = os.path.expanduser(p)
    if os.path.isabs(expanded):
        logger.debug("resolve_path: %s is absolute", expanded)
        return expanded

    dirs = list(search_dirs or _get_search_dirs())
    for d in dirs:
        candidate = os.path.join(d, expanded)
        if os.path.isfile(candidate):
            logger.info("resolve_path: found %s in %s", expanded, d)
            return candidate

    fallback = os.path.join(dirs[0], expanded) if dirs else os.path.abspath(expanded)
    logger.warning("resolve_path: %s not found in any search dir, fallback=%s", expanded, fallback)
    return fallback


def handle_pre_read(hook_input):
    tool_input = hook_input.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    logger.info("=== pre_read === file_path=%s", file_path)

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
        cache = _load_cache()
        description = _check_cache(cache, resolved)
        if not description:
            description = call_multimodal_api(config, resolved)
            _update_cache(cache, resolved, description)
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
        logger.error("pre_read: API call failed: %s: %s\nFull traceback:", type(e).__name__, e, exc_info=True)
        return {
            "continue": False,
            "stopReason": f"Multimodal model call failed: {type(e).__name__}: {e}",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": (
                    f"[Error: Failed to analyze {resolved} with multimodal model.\n"
                    f"{type(e).__name__}: {e}\n\n"
                    f"The raw image data was NOT passed to Claude. "
                    f"Check ~/.claude/image_handler.log for details."
                ),
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Multimodal model call failed ({type(e).__name__}: {e})"
                ),
            },
        }


def handle_user_prompt(hook_input):
    prompt_text = ""
    for key in ("prompt", "message", "content", "user_message", "text"):
        val = hook_input.get(key, "")
        if val:
            if isinstance(val, (dict, list)):
                prompt_text = json.dumps(val)
            else:
                prompt_text = str(val)
            break

    logger.info("=== user_prompt === text=%s", prompt_text[:300])

    if not prompt_text:
        logger.debug("user_prompt: no text, continuing")
        return {"continue": True}

    config = load_config()
    if not config:
        logger.warning("user_prompt: no config, continuing")
        return {"continue": True}

    paths = find_image_paths_in_text(prompt_text)
    logger.info("user_prompt: detected paths=%s", paths)
    cache = _load_cache()
    descriptions = []
    for p in paths:
        resolved = resolve_path(p)
        if os.path.isfile(resolved) and is_image_file(resolved):
            desc = _check_cache(cache, resolved)
            if not desc:
                try:
                    desc = call_multimodal_api(config, resolved)
                    _update_cache(cache, resolved, desc)
                except Exception as e:
                    logger.error("user_prompt: analysis failed for %s: %s: %s", resolved, type(e).__name__, e, exc_info=True)
                    desc = f"Analysis failed: {type(e).__name__}: {e}"
            descriptions.append(f"[{resolved}]\n{desc}")
        else:
            logger.debug("user_prompt: skipping %s (resolved=%s, exists=%s, is_image=%s)",
                         p, resolved, os.path.isfile(resolved), is_image_file(resolved) if os.path.isfile(resolved) else "N/A")

    if descriptions:
        combined = "\n\n---\n\n".join(descriptions)
        model_name = config.get("model", "")
        # Use systemMessage (not additionalContext) because additionalContext injects
        # a 'user' role message, which some API endpoints reject (consecutive user
        # messages not allowed). systemMessage injects as a system-level message.
        analysis_block = (
            f"[Multimodal Model ({model_name}) Analysis of referenced images]\n"
            f"{combined}\n\n"
            f"---\n"
            f"Note: These images were analyzed by an external multimodal model. "
            f"Only text descriptions are available, not raw image data."
        )
        return {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "systemMessage": analysis_block,
            },
        }

    return {"continue": True}


def main():
    parser = argparse.ArgumentParser(description="Multimodal image handler for Claude Code hooks")
    parser.add_argument(
        "--event",
        required=True,
        choices=["pre_read", "user_prompt"],
        help="Hook event type",
    )
    args = parser.parse_args()

    logger.info("=== main === event=%s", args.event)

    try:
        hook_input = json.load(sys.stdin)
        logger.info("main: hook_input=%s", json.dumps(hook_input)[:500])
    except json.JSONDecodeError as e:
        logger.error("main: JSON decode error: %s", e)
        print(json.dumps({"continue": True}))
        return
    except Exception as e:
        logger.error("main: stdin read error: %s: %s", type(e).__name__, e)
        print(json.dumps({"continue": True}))
        return

    handlers = {
        "pre_read": handle_pre_read,
        "user_prompt": handle_user_prompt,
    }

    handler = handlers.get(args.event)
    if not handler:
        logger.error("main: unknown event %s", args.event)
        print(json.dumps({"continue": True}))
        return

    try:
        result = handler(hook_input)
        logger.info("main: result=%s", json.dumps(result)[:500])
        print(json.dumps(result))
    except Exception as e:
        logger.error("main: handler exception: %s: %s\nFull traceback:", type(e).__name__, e, exc_info=True)
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()