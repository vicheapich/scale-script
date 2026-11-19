"""
Send a scale weight to the Laravel webhook (POST /api/webhook/notify).

Standard library only (no extra dependencies).

WEBHOOK_METHOD=GET instead sends only the weight as a query string, e.g.
GET <WEBHOOK_URL>?weight=12.345&unit=kg (no tracking number, no signature).

Signature - matches the Laravel middleware App\\Http\\Middleware\\ValidateIPS:

    hash_hmac('sha256', json_encode(json_decode($request->getContent(), true)), $secret)

i.e. Laravel does NOT sign the raw bytes: it decodes the JSON and re-encodes it
with PHP's default json_encode(). So the body is produced here in exactly that
PHP format (compact, "/" escaped as "\\/", non-ASCII as \\uXXXX), which makes
the bytes we sign == the bytes we send == what PHP re-encodes.
The secret is used as literal text (UTF-8), not hex-decoded - PHP's
hash_hmac() uses the key string as-is.
"""

import hashlib
import hmac
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

SOURCE = "METTLER_TOLEDO_IND231"

# UPU S10 item identifier, e.g. EE123456789KH
S10_RE = re.compile(r"^[A-Z]{2}\d{9}[A-Z]{2}$")


# ------------------------------------------------------------- config ---

def env_file_candidates():
    """.env next to the script/exe first, then the current directory."""
    if getattr(sys, "frozen", False):          # PyInstaller exe
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    paths = [os.path.join(base, ".env"), os.path.join(os.getcwd(), ".env")]
    return list(dict.fromkeys(paths))


def load_env(paths=None):
    """Minimal .env reader (KEY=VALUE, # comments, optional quotes).

    Real environment variables take precedence over the file.
    """
    values = {}
    for path in paths or env_file_candidates():
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                values.setdefault(key, val)
        break  # first .env found wins
    for key in ("WEBHOOK_URL", "WEBHOOK_SECRET", "WEBHOOK_TIMEOUT", "WEBHOOK_METHOD"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


class WebhookConfig:
    def __init__(self, url, secret, timeout=10.0, method="POST"):
        self.url = url
        self.secret = secret
        self.timeout = timeout
        self.method = method    # POST = signed JSON body, GET = ?weight=&unit= only

    @classmethod
    def from_env(cls, values=None):
        values = load_env() if values is None else values
        url = (values.get("WEBHOOK_URL") or "").strip()
        secret = values.get("WEBHOOK_SECRET") or ""
        method = (values.get("WEBHOOK_METHOD") or "POST").strip().upper()
        errors = []
        if not url:
            errors.append("WEBHOOK_URL is not set")
        elif not url.lower().startswith(("https://", "http://")):
            errors.append("WEBHOOK_URL must start with https://")
        if method not in ("POST", "GET"):
            errors.append("WEBHOOK_METHOD must be POST or GET")
        elif method == "POST" and not secret:
            errors.append("WEBHOOK_SECRET is not set")
        try:
            timeout = float(values.get("WEBHOOK_TIMEOUT") or 10)
            if timeout <= 0:
                raise ValueError
        except ValueError:
            errors.append("WEBHOOK_TIMEOUT must be a positive number")
            timeout = 10.0
        if errors:
            raise ValueError("; ".join(errors))
        return cls(url, secret, timeout, method)


# ------------------------------------------------------------ payload ---

def normalize_tracking(text):
    """Upper-case and strip spaces/dashes (barcode scanners may add them)."""
    return re.sub(r"[\s\-]", "", text or "").upper()


def s10_check_digit_ok(code):
    """UPU S10 check digit (weights 8 6 4 2 3 5 9 7, mod 11)."""
    digits = [int(c) for c in code[2:10]]
    total = sum(d * w for d, w in zip(digits, (8, 6, 4, 2, 3, 5, 9, 7)))
    check = 11 - total % 11
    check = {10: 0, 11: 5}.get(check, check)
    return check == int(code[10])


def validate_weight(value, unit):
    """Return the weight as a JSON number, or raise ValueError."""
    try:
        w = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid weight {value!r}")
    if w != w or w in (float("inf"), float("-inf")):
        raise ValueError(f"invalid weight {value!r}")
    if w <= 0:
        raise ValueError(f"weight must be greater than zero (got {value} {unit})")
    if unit not in ("kg", "g", "lb", "oz"):
        raise ValueError(f"unsupported weight unit {unit!r}")
    # Integral values are sent as integers so PHP re-encodes them identically.
    return int(w) if w.is_integer() else w


def build_payload(tracking, weight, unit):
    return [{
        "MAILITM_FID": tracking,
        "WEIGHT": validate_weight(weight, unit),
        "WEIGHT_UNIT": unit,
        "SOURCE": SOURCE,
    }]


def build_query(weight, unit):
    """GET mode: only the weight, e.g. {"weight": 12.345, "unit": "kg"}."""
    return {"weight": validate_weight(weight, unit), "unit": unit}


def query_url(url, query):
    """Append the query to the URL, e.g. ...?weight=12.345&unit=kg"""
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)


def php_json_encode(obj):
    """Bytes identical to PHP json_encode($obj) with default flags."""
    text = json.dumps(obj, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    # PHP escapes "/" by default; "/" can only occur inside strings in JSON.
    return text.replace("/", "\\/").encode("ascii")


def sign(body, secret):
    """Lower-case hex HMAC-SHA256 of the exact body bytes."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- send ---

def sanitize(text, secret=None, limit=500):
    """Make a response body safe to print: no secret, no control chars, short."""
    if secret:
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "?", text)
    return text if len(text) <= limit else text[:limit] + "... (truncated)"


class SendResult:
    def __init__(self, ok, status=None, message="", body=""):
        self.ok = ok            # True only if HTTP 2xx AND JSON "success": true
        self.status = status    # HTTP status, or None for network errors
        self.message = message
        self.body = body        # sanitized response body


def send(config, payload, opener=None):
    """POST the payload (or GET with build_query() output). Never raises; returns a SendResult."""
    if config.method == "GET":
        request = urllib.request.Request(
            query_url(config.url, payload),
            method="GET",
            headers={"Accept": "application/json", "User-Agent": "scale-test-script"},
        )
    else:
        body = php_json_encode(payload)
        request = urllib.request.Request(
            config.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "signature": sign(body, config.secret),
                "User-Agent": "scale-test-script",
            },
        )
    urlopen = opener or urllib.request.urlopen
    try:
        with urlopen(request, timeout=config.timeout) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:      # 4xx / 5xx still have a body
        with e:
            status, raw = e.code, e.read()
    except urllib.error.URLError as e:
        return SendResult(False, None, f"Network error: {e.reason}")
    except TimeoutError:
        return SendResult(False, None, f"Timed out after {config.timeout} s")
    except OSError as e:
        return SendResult(False, None, f"Network error: {e}")

    text = sanitize(raw.decode("utf-8", errors="replace"), config.secret)
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    api_success = isinstance(data, dict) and data.get("success") is True
    api_message = data.get("message") if isinstance(data, dict) else None

    if 200 <= status < 300 and api_success:
        return SendResult(True, status, api_message or "OK", text)
    if 200 <= status < 300:
        return SendResult(False, status, "HTTP 2xx but the API did not return success=true", text)
    return SendResult(False, status, api_message or f"HTTP {status}", text)
