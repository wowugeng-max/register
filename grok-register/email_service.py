"""
邮箱服务类
对外保持接口不变：
- create_email() -> (token_like, email)
- fetch_first_email(token_like) -> str | None

支持 provider:
- gptmail（默认）
- luckmail（购买邮箱 + token 轮询）
"""

import json
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from curl_cffi import requests

try:
    from luckmail import LuckMailClient
    from luckmail.exceptions import LuckMailError
except Exception:
    LuckMailClient = None
    class LuckMailError(Exception):
        pass

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"

LUCKMAIL_BASE_URL = str(os.getenv("LUCKMAIL_BASE_URL") or "https://mails.luckyous.com").strip().rstrip("/")
LUCKMAIL_API_KEY = str(os.getenv("LUCKMAIL_API_KEY") or "").strip()
LUCKMAIL_API_SECRET = str(os.getenv("LUCKMAIL_API_SECRET") or "").strip()
LUCKMAIL_USE_HMAC = str(os.getenv("LUCKMAIL_USE_HMAC") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
LUCKMAIL_PROJECT_CODE = str(os.getenv("LUCKMAIL_PROJECT_CODE") or "grok").strip()
LUCKMAIL_EMAIL_TYPE = str(os.getenv("LUCKMAIL_EMAIL_TYPE") or "ms_graph").strip()
LUCKMAIL_DOMAIN = str(os.getenv("LUCKMAIL_DOMAIN") or "outlook.com").strip()


class GPTMailClient:
    """与现有 grok-register 保持一致的 GPTMail 访问方式"""

    def __init__(self, proxies: Any = None):
        self.base_url = "https://mail.chatgpt.org.uk"
        self.session = requests.Session(proxies=proxies, impersonate="chrome")
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": f"{self.base_url}/",
            }
        )

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

    def generate_email(self) -> str:
        self._init_browser_session()
        resp = self.session.get(f"{self.base_url}/api/generate-email", timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"GPTMail 生成失败: {resp.status_code}")
        data = resp.json()
        email = str(((data.get("data") or {}).get("email") or "")).strip()
        token = str(((data.get("auth") or {}).get("token") or "")).strip()
        if token:
            self.session.headers.update({"x-inbox-token": token})
        if not email:
            raise RuntimeError("GPTMail 返回邮箱为空")
        return email

    def list_emails(self, email: str) -> List[Dict[str, Any]]:
        encoded_email = urllib.parse.quote(email)
        url = f"{self.base_url}/api/emails?email={encoded_email}"
        resp = self.session.get(url, timeout=15)
        if resp.status_code == 200:
            return ((resp.json() or {}).get("data") or {}).get("emails") or []
        return []


class LuckMailInbox:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str = "",
        use_hmac: bool = False,
        project_code: str = "grok",
        email_type: str = "ms_graph",
        domain: str = "outlook.com",
        timeout: int = 30,
    ):
        if LuckMailClient is None:
            raise RuntimeError("LuckMail SDK 不可用")
        if not base_url:
            raise RuntimeError("缺少 LUCKMAIL_BASE_URL")
        if not api_key:
            raise RuntimeError("缺少 LUCKMAIL_API_KEY")

        self.client = LuckMailClient(
            base_url=base_url,
            api_key=api_key,
            api_secret=api_secret or None,
            use_hmac=bool(use_hmac),
            timeout=float(timeout),
        )
        self.project_code = project_code or "grok"
        self.email_type = email_type or "ms_graph"
        self.domain = domain or "outlook.com"
        self.address = ""
        self.token = ""

    def create_email(self):
        try:
            result = self.client.user.purchase_emails(
                project_code=self.project_code,
                quantity=1,
                email_type=self.email_type,
                domain=self.domain,
            )
        except LuckMailError as e:
            raise RuntimeError(f"LuckMail 购买邮箱失败: {e}") from e
        except Exception as e:
            raise RuntimeError(f"LuckMail 初始化失败: {e}") from e

        purchases = list((result or {}).get("purchases") or [])
        if not purchases:
            raise RuntimeError("LuckMail 购买邮箱失败：未返回 purchases")

        purchase = purchases[0] or {}
        self.address = str(purchase.get("email_address") or "").strip()
        self.token = str(purchase.get("token") or "").strip()
        if not self.address or not self.token:
            raise RuntimeError("LuckMail 购买邮箱失败：缺少 email_address 或 token")
        return {"provider": "luckmail", "token": self.token, "email": self.address, "client": self}, self.address

    def fetch_first_email(self) -> Optional[str]:
        if not self.token:
            return None
        try:
            result = self.client.user.get_token_code(self.token)
            chunks: List[str] = []
            if getattr(result, "verification_code", None):
                chunks.append(str(getattr(result, "verification_code", "") or ""))
            if getattr(result, "mail", None):
                chunks.append(json.dumps(getattr(result, "mail", None) or {}, ensure_ascii=False))

            mail_list = self.client.user.get_token_mails(self.token)
            mails = list(getattr(mail_list, "mails", []) or [])
            if mails:
                mail = mails[0]
                message_id = str(getattr(mail, "message_id", "") or "").strip()
                chunks.extend([
                    str(getattr(mail, "subject", "") or ""),
                    str(getattr(mail, "body", "") or ""),
                    str(getattr(mail, "html_body", "") or ""),
                ])
                if message_id:
                    try:
                        detail = self.client.user.get_token_mail_detail(self.token, message_id)
                        chunks.extend([
                            str(getattr(detail, "subject", "") or ""),
                            str(getattr(detail, "body_text", "") or ""),
                            str(getattr(detail, "body_html", "") or ""),
                            str(getattr(detail, "verification_code", "") or ""),
                        ])
                    except Exception:
                        pass
            text = "\n".join([c for c in chunks if c])
            return text or None
        except Exception as e:
            print(f"获取 LuckMail 邮件失败: {e}")
            return None


class EmailService:
    """统一邮箱服务门面，兼容旧调用方"""

    def __init__(self, proxies: Any = None, provider: str = "gptmail"):
        self.proxies = proxies
        self.provider = str(provider or os.getenv("EMAIL_PROVIDER") or "gptmail").strip().lower()
        if self.provider not in {"gptmail", "luckmail"}:
            raise ValueError(f"不支持的邮箱提供商: {self.provider}")

    def create_email(self):
        if self.provider == "luckmail":
            try:
                inbox = LuckMailInbox(
                    base_url=LUCKMAIL_BASE_URL,
                    api_key=LUCKMAIL_API_KEY,
                    api_secret=LUCKMAIL_API_SECRET,
                    use_hmac=LUCKMAIL_USE_HMAC,
                    project_code=LUCKMAIL_PROJECT_CODE,
                    email_type=LUCKMAIL_EMAIL_TYPE,
                    domain=LUCKMAIL_DOMAIN,
                )
                token_like, email = inbox.create_email()
                print(f"[+] LuckMail 邮箱已购买: {email}")
                return token_like, email
            except Exception as e:
                print(f"[Error] 请求 LuckMail 出错: {e}")
                return None, None

        try:
            client = GPTMailClient(self.proxies)
            email = client.generate_email()
            token_like = {"provider": "gptmail", "client": client, "email": email}
            return token_like, email
        except Exception as e:
            print(f"[Error] 请求 GPTMail API 出错: {e}")
            return None, None

    def fetch_first_email(self, token_like):
        try:
            if not isinstance(token_like, dict):
                return None

            provider = str(token_like.get("provider") or "gptmail").strip().lower()
            if provider == "luckmail":
                client = token_like.get("client")
                if not client:
                    return None
                return client.fetch_first_email()

            client: Optional[GPTMailClient] = token_like.get("client")
            email = str(token_like.get("email") or "").strip()
            if not client or not email:
                return None

            emails = client.list_emails(email)
            if not emails:
                return None

            first = emails[0] or {}
            subject = str(first.get("subject") or "")
            from_name = str(((first.get("from") or {}).get("name") or ""))
            from_email = str(((first.get("from") or {}).get("address") or first.get("from_address") or ""))
            body_text = str(first.get("text") or first.get("content") or "")
            body_html = str(first.get("html") or first.get("html_content") or "")
            return "\n".join([f">{subject}<", subject, from_name, from_email, body_text, body_html])
        except Exception as e:
            print(f"获取邮件失败: {e}")
            return None
