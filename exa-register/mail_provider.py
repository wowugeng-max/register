"""
统一邮箱 provider 抽象。
当前支持：
1. Cloudflare 自定义邮件 API
2. DuckMail API
3. GPTMail
4. TempMail.lol
"""
import html
import random
import re
import string
import time
import urllib.parse
from pathlib import Path

import requests as std_requests

from config import (
    DUCKMAIL_API_KEY,
    DUCKMAIL_API_URL,
    DUCKMAIL_DOMAIN,
    DUCKMAIL_DOMAINS,
    EMAIL_API_TOKEN,
    EMAIL_API_URL,
    EMAIL_DOMAIN,
    EMAIL_DOMAINS,
    EMAIL_POLL_INTERVAL,
    EMAIL_PROVIDER,
)

_DUCKMAIL_DOMAIN_PRIORITY = (
    "baldur.edu.kg",
    "duckmail.sbs",
)
_DUCKMAIL_DOMAIN_CACHE = None
_DUCKMAIL_MAILBOX_CACHE = {}
_SELECTED_DOMAIN = ""
_GPTMAIL_BASE = "https://mail.chatgpt.org.uk"
_HERE = Path(__file__).resolve().parent
_BANNED_DOMAINS_FILE = _HERE / "banned_email_domains.txt"
_GPTMAIL_CLIENTS = {}  # email -> client
_TEMPMAIL_BASE = "https://api.tempmail.lol"
_TEMPMAIL_INBOXES = {}  # email -> client


class GPTMailClient:
    def __init__(self, proxies=None):
        self.session = std_requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": _GPTMAIL_BASE,
            }
        )
        if proxies:
            self.session.proxies.update(proxies)
        self.base_url = _GPTMAIL_BASE

    def _init_browser_session(self):
        try:
            resp = self.session.get(self.base_url, timeout=15)
            gm_sid = self.session.cookies.get("gm_sid")
            if gm_sid:
                self.session.headers.update({"Cookie": f"gm_sid={gm_sid}"})
            token_match = re.search(r"(eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+)", resp.text)
            if token_match:
                self.session.headers.update({"x-inbox-token": token_match.group(1)})
        except Exception:
            pass

    def generate_email(self):
        self._init_browser_session()
        resp = self.session.get(f"{self.base_url}/api/generate-email", timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            email = data.get("data", {}).get("email")
            token = (data.get("auth") or {}).get("token")
            if email and token:
                self.session.headers.update({"x-inbox-token": token})
                return email, token
        raise RuntimeError(f"GPTMail 生成失败: HTTP {resp.status_code}")

    def set_token(self, token):
        if token:
            self.session.headers.update({"x-inbox-token": token})

    def list_emails(self, email):
        encoded_email = urllib.parse.quote(email)
        resp = self.session.get(f"{self.base_url}/api/emails?email={encoded_email}", timeout=15)
        if resp.status_code == 200:
            return resp.json().get("data", {}).get("emails", [])
        return []


class TempMailClient:
    def __init__(self, proxies=None):
        self.session = std_requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        if proxies:
            self.session.proxies.update(proxies)
        self.base_url = _TEMPMAIL_BASE
        self.token = None

    def generate_email(self):
        response = self.session.post(f"{self.base_url}/v2/inbox/create", json={}, timeout=15)
        response.raise_for_status()
        data = response.json()
        email = data.get("address")
        token = data.get("token")
        if not email or not token:
            raise RuntimeError(f"TempMail 生成失败: {data}")
        self.token = token
        return email, token

    def set_token(self, token):
        self.token = token

    def list_emails(self):
        if not self.token:
            raise RuntimeError("TempMail token 缺失")
        response = self.session.get(
            f"{self.base_url}/v2/inbox",
            params={"token": self.token},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("emails", [])


def _get_gptmail_client(email=None):
    if email and email in _GPTMAIL_CLIENTS:
        return _GPTMAIL_CLIENTS[email]
    return GPTMailClient()


def _get_tempmail_client(email=None):
    if email and email in _TEMPMAIL_INBOXES:
        return _TEMPMAIL_INBOXES[email]
    return TempMailClient()


def _load_banned_items(path):
    if not path.exists():
        return set()
    try:
        return {line.strip().lower() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    except Exception:
        return set()


def _append_banned_item(path, value):
    value = (value or "").strip().lower()
    if not value:
        return
    existing = _load_banned_items(path)
    if value in existing:
        return
    with path.open("a", encoding="utf-8") as f:
        f.write(value + "\n")


def _root_domain(domain):
    domain = (domain or "").strip().lower().strip(".")
    if not domain:
        return ""
    parts = [p for p in domain.split(".") if p]
    if len(parts) <= 2:
        return domain
    return ".".join(parts[-2:])


def get_banned_domains():
    return _load_banned_items(_BANNED_DOMAINS_FILE)


def is_banned_email(email):
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    domain = email.split("@", 1)[1]
    root_domain = _root_domain(domain)
    banned = get_banned_domains()
    return domain in banned or root_domain in banned


def mark_banned_email(email, reason=""):
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return
    domain = email.split("@", 1)[1]
    root_domain = _root_domain(domain)
    if not root_domain:
        return
    _append_banned_item(_BANNED_DOMAINS_FILE, root_domain)
    if reason:
        print(f"[mail] 已加入 ban 主域名列表: {root_domain}（原域名 {domain}），原因: {reason}")
    else:
        print(f"[mail] 已加入 ban 主域名列表: {root_domain}（原域名 {domain}）")


def rand_str(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def get_configured_domains():
    """返回当前 provider 在配置里声明的可选域名。"""
    if EMAIL_PROVIDER == "duckmail":
        return DUCKMAIL_DOMAINS[:]
    if EMAIL_PROVIDER in {"gptmail", "tempmail", "auto"}:
        return []
    return EMAIL_DOMAINS[:]


def get_active_domain():
    """返回当前实际使用的域名。"""
    if _SELECTED_DOMAIN:
        return _SELECTED_DOMAIN

    configured = get_configured_domains()
    if configured:
        return configured[0]

    if EMAIL_PROVIDER == "duckmail":
        return DUCKMAIL_DOMAIN
    if EMAIL_PROVIDER in {"gptmail", "tempmail", "auto"}:
        return ""
    return EMAIL_DOMAIN


def set_selected_domain(domain):
    """设置本轮运行使用的域名。"""
    global _SELECTED_DOMAIN
    _SELECTED_DOMAIN = (domain or "").strip()


def _normalize_service(service):
    return "exa"


def _username_prefix(service):
    return "exa"


def create_email(service="exa", max_attempts=20):
    """按当前 provider 生成邮箱与强密码，自动跳过 ban 主域名。"""
    prefix = _username_prefix(service)

    for _ in range(max_attempts):
        password = f"Tv{rand_str(6)}{random.randint(100, 999)}!A"
        actual_provider = EMAIL_PROVIDER

        if EMAIL_PROVIDER == "duckmail":
            email = _create_duckmail_mailbox(password, prefix)
        elif EMAIL_PROVIDER == "gptmail":
            client = GPTMailClient()
            email, token = client.generate_email()
            _GPTMAIL_CLIENTS[email] = client
            client.set_token(token)
        elif EMAIL_PROVIDER == "tempmail":
            client = TempMailClient()
            email, token = client.generate_email()
            _TEMPMAIL_INBOXES[email] = client
            client.set_token(token)
        elif EMAIL_PROVIDER == "auto":
            try:
                client = TempMailClient()
                email, token = client.generate_email()
                _TEMPMAIL_INBOXES[email] = client
                client.set_token(token)
                actual_provider = "tempmail"
            except Exception as exc:
                print(f"⚠️ TempMail 初始化失败，回退 GPTMail: {exc}")
                client = GPTMailClient()
                email, token = client.generate_email()
                _GPTMAIL_CLIENTS[email] = client
                client.set_token(token)
                actual_provider = "gptmail"
        else:
            username = f"{prefix}-{rand_str()}"
            email = f"{username}@{get_active_domain()}"

        if is_banned_email(email):
            print(f"[mail] 跳过 ban 主域名邮箱: {email}")
            continue

        print(f"✅ 邮箱({actual_provider}): {email}")
        return email, password

    raise RuntimeError("邮箱生成失败：连续命中 ban 主域名次数过多")


def get_verification_link(email, timeout=120):
    """等待验证邮件并提取验证链接。"""
    print(f"⏳ 等待验证邮件（最多 {timeout} 秒）...", end="", flush=True)
    return _poll_mailbox(
        email=email,
        timeout=timeout,
        extractor=_extract_verification_link,
        found_message="\n✅ 找到验证链接",
        timeout_message="\n❌ 验证邮件超时",
        error_prefix="检查验证邮件失败",
        dot_progress=True,
    )


def get_email_code(email, timeout=120, service="exa"):
    """等待邮箱里的 6 位验证码。"""
    print(f"📨 等待邮箱验证码（最多 {timeout} 秒）...")
    return _poll_mailbox(
        email=email,
        timeout=timeout,
        extractor=lambda message: _extract_email_code(message, service=service),
        found_message="✅ 收到 6 位验证码",
        timeout_message="❌ 等待邮箱验证码超时",
        error_prefix="读取邮箱验证码失败",
        dot_progress=False,
    )


def _poll_mailbox(email, timeout, extractor, found_message, timeout_message, error_prefix, dot_progress):
    start_time = time.time()
    seen_ids = set()
    poll_count = 0

    while time.time() - start_time < timeout:
        poll_count += 1
        try:
            for message in _iter_messages(email):
                message_id = _message_id(message)
                if message_id and message_id in seen_ids:
                    continue
                if message_id:
                    seen_ids.add(message_id)

                result = extractor(message)
                if result:
                    print(found_message)
                    return result
        except Exception as exc:
            print(f"⚠️  {error_prefix}: {exc}")

        time.sleep(EMAIL_POLL_INTERVAL)
        if dot_progress:
            print(".", end="", flush=True)
        else:
            print(f"[mail] poll #{poll_count}, seen={len(seen_ids)}")

    try:
        for message in _iter_messages(email):
            result = extractor(message)
            if result:
                print(found_message)
                return result
    except Exception as exc:
        print(f"⚠️  {error_prefix}: {exc}")

    print(timeout_message)
    return None


def _extract_verification_link(message):
    subject = (message.get("subject") or "").lower()
    sender = (message.get("from") or message.get("message_from") or "").lower()
    content = _message_content(message)
    urls = [
        html.unescape(raw).rstrip(").,;")
        for raw in re.findall(r'https://[^\s<>"\']+', content, re.IGNORECASE)
    ]

    primary_link_hints = ("verif", "confirm", "magic", "auth", "callback", "signin", "signup")
    primary_host_hints = ("exa", "clerk", "stytch", "auth", "login")
    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in primary_link_hints) and any(host in lowered for host in primary_host_hints):
            return url

    combined = f"{sender} {subject} {content[:4000]}".lower()
    message_hints = ("verify", "verification", "confirm", "magic link", "sign in", "exa")
    if not any(token in combined for token in message_hints):
        return None

    for url in urls:
        lowered = url.lower()
        if any(token in lowered for token in primary_link_hints):
            return url

    return None


def _extract_email_code(message, service="exa"):
    service = _normalize_service(service)
    subject = (message.get("subject") or "")
    sender = (message.get("from") or message.get("message_from") or "")
    text = message.get("text") or ""
    html_content = message.get("html") or ""
    content = _message_content(message)
    combined = "\n".join([subject, sender, text, html_content, content])
    combined_lower = combined.lower()

    if service == "exa":
        priority_patterns = [
            r"(?:sign\s*in|login|log\s*in|verification|verify|one[ -]?time|temporary|security|passcode|code|otp)[^0-9]{0,80}(\d{6})",
            r"(\d{6})[^0-9]{0,80}(?:sign\s*in|login|log\s*in|verification|verify|one[ -]?time|temporary|security|passcode|code|otp)",
        ]
        for source in (subject, text, html_content, content, combined):
            for pat in priority_patterns:
                match = re.search(pat, source, re.IGNORECASE | re.DOTALL)
                if match:
                    return match.group(1)

        exa_hints = (
            "exa",
            "auth.exa.ai",
            "dashboard.exa.ai",
            "sign in",
            "verify",
            "verification",
            "code",
            "otp",
        )
        if any(hint in combined_lower for hint in exa_hints):
            codes = re.findall(r"(?<!\d)(\d{6})(?!\d)", combined)
            if codes:
                return codes[0]
        return None

    for source in (text, content):
        match = re.search(r"\b(\d{6})\b", source)
        if match:
            return match.group(1)
    return None


def _gptmail_iter_messages(email):
    client = _get_gptmail_client(email)
    for message in client.list_emails(email):
        yield {
            "subject": message.get("subject") or "",
            "from": message.get("from") or message.get("sender") or message.get("from_address") or "",
            "text": message.get("text") or message.get("content") or "",
            "html": message.get("html") or message.get("html_content") or "",
            "id": message.get("id") or message.get("_id") or message.get("email_id") or "",
        }


def _tempmail_iter_messages(email):
    client = _get_tempmail_client(email)
    for message in client.list_emails():
        yield {
            "subject": message.get("subject") or "",
            "from": message.get("from") or message.get("sender") or message.get("from_address") or "",
            "text": message.get("body") or message.get("text") or message.get("content") or "",
            "html": message.get("html") or message.get("html_body") or message.get("body_html") or "",
            "id": message.get("id") or message.get("email_id") or message.get("msgid") or "",
        }


def _iter_messages(email):
    if EMAIL_PROVIDER == "duckmail":
        yield from _duckmail_iter_messages(email)
        return
    if EMAIL_PROVIDER == "gptmail":
        yield from _gptmail_iter_messages(email)
        return
    if EMAIL_PROVIDER == "tempmail":
        yield from _tempmail_iter_messages(email)
        return
    if EMAIL_PROVIDER == "auto":
        if email in _TEMPMAIL_INBOXES:
            yield from _tempmail_iter_messages(email)
            return
        if email in _GPTMAIL_CLIENTS:
            yield from _gptmail_iter_messages(email)
            return

    yield from _cloudflare_iter_messages(email)


def _cloudflare_iter_messages(email):
    response = std_requests.get(
        f"{EMAIL_API_URL}/messages",
        params={"address": email},
        headers={"Authorization": f"Bearer {EMAIL_API_TOKEN}"},
        timeout=10,
    )
    response.raise_for_status()

    for message in response.json().get("messages", []):
        yield message


def _duckmail_iter_messages(email):
    token = _duckmail_get_token(email)
    response = _duckmail_request("GET", "/messages", token=token)

    if response.status_code == 401:
        token = _duckmail_get_token(email, refresh=True)
        response = _duckmail_request("GET", "/messages", token=token)

    response.raise_for_status()

    for message in response.json().get("hydra:member", []):
        message_id = message.get("id")
        if not message_id:
            continue

        detail = _duckmail_request("GET", f"/messages/{message_id}", token=token)
        if detail.status_code == 401:
            token = _duckmail_get_token(email, refresh=True)
            detail = _duckmail_request("GET", f"/messages/{message_id}", token=token)
        detail.raise_for_status()
        yield detail.json()


def _create_duckmail_mailbox(password, prefix):
    domain = _choose_duckmail_domain()

    for _ in range(5):
        username = f"{prefix}-{rand_str()}"
        email = f"{username}@{domain}"
        response = _duckmail_request(
            "POST",
            "/accounts",
            json={"address": email, "password": password},
            use_api_key=True,
        )

        if response.status_code == 201:
            account = response.json()
            token = _duckmail_issue_token(email, password)
            _DUCKMAIL_MAILBOX_CACHE[email] = {
                "account_id": account.get("id", ""),
                "password": password,
                "token": token,
            }
            return email

        if response.status_code not in (409, 422):
            response.raise_for_status()

        message = _response_error_message(response).lower()
        if "exists" in message or "already" in message or response.status_code == 409:
            continue

        raise RuntimeError(f"DuckMail 创建邮箱失败: {_response_error_message(response)}")

    raise RuntimeError("DuckMail 邮箱创建失败：随机地址重复次数过多")


def _choose_duckmail_domain():
    domains = _duckmail_domains()
    selected = get_active_domain()
    configured = get_configured_domains()

    if selected:
        if selected not in domains:
            raise RuntimeError(
                f"配置的 DuckMail 域名不可用: {selected}，当前可用域名: {', '.join(domains)}"
            )
        return selected

    for domain in configured:
        if domain in domains:
            return domain

    for domain in _DUCKMAIL_DOMAIN_PRIORITY:
        if domain in domains:
            return domain

    return domains[0]


def _duckmail_domains():
    global _DUCKMAIL_DOMAIN_CACHE
    if _DUCKMAIL_DOMAIN_CACHE is not None:
        return _DUCKMAIL_DOMAIN_CACHE

    response = _duckmail_request("GET", "/domains", use_api_key=True)
    response.raise_for_status()
    domains = [
        item.get("domain")
        for item in response.json().get("hydra:member", [])
        if item.get("domain")
    ]

    if not domains:
        raise RuntimeError("DuckMail 未返回可用域名")

    _DUCKMAIL_DOMAIN_CACHE = domains
    return domains


def _duckmail_get_token(email, refresh=False):
    mailbox = _DUCKMAIL_MAILBOX_CACHE.get(email)
    if not mailbox:
        raise RuntimeError("DuckMail 邮箱上下文不存在，请重新生成邮箱后再试")

    if mailbox.get("token") and not refresh:
        return mailbox["token"]

    mailbox["token"] = _duckmail_issue_token(email, mailbox["password"])
    return mailbox["token"]


def _duckmail_issue_token(email, password):
    response = _duckmail_request(
        "POST",
        "/token",
        json={"address": email, "password": password},
    )
    response.raise_for_status()

    token = response.json().get("token")
    if not token:
        raise RuntimeError("DuckMail 登录成功但未返回 token")
    return token


def _duckmail_request(method, path, token=None, use_api_key=False, **kwargs):
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif use_api_key and DUCKMAIL_API_KEY:
        headers["Authorization"] = f"Bearer {DUCKMAIL_API_KEY}"

    if "json" in kwargs:
        headers.setdefault("Content-Type", "application/json")

    return std_requests.request(
        method,
        f"{DUCKMAIL_API_URL.rstrip('/')}{path}",
        headers=headers,
        timeout=kwargs.pop("timeout", 15),
        **kwargs,
    )


def _message_id(message):
    return message.get("id") or message.get("msgid") or message.get("email_id")


def _message_content(message):
    html_part = message.get("html") or ""
    if isinstance(html_part, list):
        html_part = " ".join(str(item) for item in html_part)
    text_part = message.get("text") or ""
    return f"{html_part} {text_part}"


def _response_error_message(response):
    try:
        data = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"

    if isinstance(data, dict):
        return data.get("message") or data.get("detail") or data.get("error") or str(data)
    return str(data)
