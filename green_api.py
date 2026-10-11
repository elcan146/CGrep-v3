import json
import os
import re
import threading

try:
    import requests
except Exception as _requests_exc:  # pragma: no cover
    requests = None

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_RECIPIENTS_LOCK = threading.Lock()


def _load_config_env():
    path = os.path.join(_BASE_DIR, "config.env")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())


_load_config_env()


def _env(key, default=""):
    return os.environ.get(key, default).strip()


GREEN_API_ID = _env("GREEN_API_ID")
GREEN_API_TOKEN = _env("GREEN_API_TOKEN")

OPENWA_BASE_URL = _env("OPENWA_BASE_URL", "http://localhost:2785/api").rstrip("/")
OPENWA_API_KEY = _env("OPENWA_API_KEY")
OPENWA_SESSION = _env("OPENWA_SESSION", "default")

_NUMBERS_PATH = os.path.join(_BASE_DIR, "whatsapp_numbers.json")


def is_configured():
    """Return True if either OpenWA or Green API is configured."""
    return bool(OPENWA_BASE_URL and OPENWA_API_KEY and OPENWA_SESSION) or bool(
        GREEN_API_ID and GREEN_API_TOKEN
    )


def _openwa_configured():
    """Return True if OpenWA is configured."""
    return bool(OPENWA_BASE_URL and OPENWA_API_KEY and OPENWA_SESSION)


def _normalize_phone(value):
    """Convert a raw phone number into Green API chatId format (digits@c.us)."""
    raw = (value or "").strip().replace(" ", "").replace("-", "").replace("+", "")
    if not raw:
        raise ValueError("empty phone number")
    if "@" not in raw:
        raw = f"{raw}@c.us"
    chat_id = raw.lower()
    if not re.fullmatch(r"\d+@c\.us", chat_id):
        raise ValueError(f"invalid phone number format: {value}")
    return chat_id


def _normalize_group(value):
    """Convert a raw WhatsApp group id into Green API chatId format (digits@g.us)."""
    raw = (value or "").strip().replace(" ", "")
    if not raw:
        raise ValueError("empty group id")
    if "@" not in raw:
        raw = f"{raw}@g.us"
    chat_id = raw.lower()
    if not re.fullmatch(r"\d+@g\.us", chat_id):
        raise ValueError(f"invalid group id format: {value}")
    return chat_id


def _load_recipients():
    """Load persisted WhatsApp recipients (numbers and groups).

    Supports the legacy flat-list format (numbers only) and the current
    object format {"numbers": [...], "groups": [...]}."""
    try:
        with open(_NUMBERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {
                    "numbers": [str(x) for x in data.get("numbers", [])],
                    "groups": [str(x) for x in data.get("groups", [])],
                }
            if isinstance(data, list):
                return {"numbers": [str(x) for x in data], "groups": []}
    except Exception:
        pass
    return {"numbers": [], "groups": []}


def _save_recipients(numbers, groups):
    """Persist numbers and groups as {"numbers": [...], "groups": [...]}."""
    with open(_NUMBERS_PATH, "w", encoding="utf-8") as f:
        json.dump({"numbers": numbers, "groups": groups}, f)


def get_numbers():
    """Load persisted WhatsApp recipient numbers."""
    return _load_recipients()["numbers"]


def get_groups():
    """Load persisted WhatsApp recipient groups."""
    return _load_recipients()["groups"]


def get_all_recipients():
    """Load all persisted WhatsApp recipients (numbers + groups)."""
    r = _load_recipients()
    return r["numbers"] + r["groups"]


def save_numbers(numbers):
    """Validate, deduplicate and persist a list of phone numbers."""
    cleaned = []
    seen = set()
    for n in numbers:
        norm = _normalize_phone(n)
        if norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    with _RECIPIENTS_LOCK:
        _save_recipients(cleaned, _load_recipients()["groups"])
    return cleaned


def save_groups(groups):
    """Validate, deduplicate and persist a list of WhatsApp group ids."""
    cleaned = []
    seen = set()
    for g in groups:
        norm = _normalize_group(g)
        if norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    with _RECIPIENTS_LOCK:
        _save_recipients(_load_recipients()["numbers"], cleaned)
    return cleaned


def send_message(chat_id, text):
    """Send a single WhatsApp text message via OpenWA or Green API."""
    if requests is None:  # pragma: no cover
        raise RuntimeError("requests package is required for WhatsApp calls")
    if not is_configured():
        raise RuntimeError("WhatsApp API is not configured (OpenWA or Green API)")

    if _openwa_configured():
        return _send_openwa(chat_id, text)
    return _send_green_api(chat_id, text)


def _send_openwa(chat_id, text):
    """Send a message through the local OpenWA gateway."""
    url = f"{OPENWA_BASE_URL}/sessions/{OPENWA_SESSION}/messages/send-text"
    payload = {"chatId": chat_id, "text": text}
    headers = {"Content-Type": "application/json", "X-API-Key": OPENWA_API_KEY}
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    body = response.text[:1000]
    print(f"[OpenWA] chatId={chat_id} status={response.status_code} body={body}")
    if response.ok:
        data = response.json() or {}
        return {
            "ok": True,
            "idMessage": data.get("idMessage") or data.get("id") or data.get("messageId"),
            "raw": data,
        }
    return {"ok": False, "status": response.status_code, "detail": body}


def _send_green_api(chat_id, text):
    """Send a message through the Green API cloud service."""
    url = (
        f"https://api.green-api.com/waInstance{GREEN_API_ID}"
        f"/sendMessage/{GREEN_API_TOKEN}"
    )
    payload = {"chatId": chat_id, "message": text}
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    body = response.text[:500]
    print(f"[Green API] chatId={chat_id} status={response.status_code} body={body}")
    if response.status_code == 200:
        data = response.json() or {}
        if data.get("idMessage"):
            return {"ok": True, "idMessage": data.get("idMessage")}
        return {"ok": False, "status": response.status_code, "detail": body, "raw": data}
    return {"ok": False, "status": response.status_code, "detail": body}


def send_test(phones=None, groups=None, message=None):
    """Send a test message to the given phones/groups (or all persisted recipients)."""
    if message is None:
        message = "Bu cgrep test mesajıdır. / This is a test message from cgrep."
    if phones is None and groups is None:
        targets = get_all_recipients()
    else:
        targets = []
        for phone in phones or []:
            targets.append(_normalize_phone(phone))
        for group in groups or []:
            targets.append(_normalize_group(group))
    if not targets:
        raise ValueError("no recipients configured")
    results = []
    ok_count = 0
    for chat_id in targets:
        try:
            res = send_message(chat_id, message)
            results.append({"phone": chat_id, **res})
            if res.get("ok"):
                ok_count += 1
        except Exception as exc:
            results.append({"phone": chat_id, "ok": False, "detail": str(exc)})
    return {
        "ok": ok_count > 0,
        "sent": ok_count,
        "total": len(targets),
        "results": results,
    }


def send_image(chat_id, image_b64, caption=None, filename="drop.png", mimetype="image/png"):
    """Send a WhatsApp image via OpenWA (Green API images are not supported here)."""
    if requests is None:  # pragma: no cover
        raise RuntimeError("requests package is required for WhatsApp calls")
    if not is_configured():
        raise RuntimeError("WhatsApp API is not configured")
    if not _openwa_configured():
        raise RuntimeError("Image sending is only supported with OpenWA")
    url = f"{OPENWA_BASE_URL}/sessions/{OPENWA_SESSION}/messages/send-image"
    payload = {
        "chatId": chat_id,
        "base64": image_b64,
        "mimetype": mimetype,
        "filename": filename,
    }
    if caption:
        payload["caption"] = caption
    headers = {"Content-Type": "application/json", "X-API-Key": OPENWA_API_KEY}
    response = requests.post(url, json=payload, headers=headers, timeout=60)
    body = response.text[:1000]
    print(f"[OpenWA] chatId={chat_id} image status={response.status_code} body={body}")
    if response.ok:
        data = response.json() or {}
        return {
            "ok": True,
            "idMessage": data.get("idMessage") or data.get("id") or data.get("messageId"),
            "raw": data,
        }
    return {"ok": False, "status": response.status_code, "detail": body}


def send_image_to_all(image_b64, caption=None, filename="drop.png", mimetype="image/png"):
    """Send an image to every persisted WhatsApp recipient."""
    targets = get_all_recipients()
    if not targets:
        raise ValueError("no recipients configured")
    results = []
    ok_count = 0
    for chat_id in targets:
        try:
            res = send_image(chat_id, image_b64, caption=caption, filename=filename, mimetype=mimetype)
            results.append({"phone": chat_id, **res})
            if res.get("ok"):
                ok_count += 1
        except Exception as exc:
            results.append({"phone": chat_id, "ok": False, "detail": str(exc)})
    return {
        "ok": ok_count > 0,
        "sent": ok_count,
        "total": len(targets),
        "results": results,
    }


print(
    f"[green_api] loaded: openwa={_openwa_configured()}, "
    f"green_api={bool(GREEN_API_ID and GREEN_API_TOKEN)}, "
    f"session={OPENWA_SESSION}"
)
