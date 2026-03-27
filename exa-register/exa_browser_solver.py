"""
使用 Camoufox 完成 Exa 注册
思路：通过邮箱验证码登录，跳过 onboarding，并提取默认 API Key
"""
import asyncio
import json
import os
import random
import re
import threading
import time
import traceback

import requests as std_requests
from camoufox.sync_api import Camoufox

from config import API_KEY_TIMEOUT, EMAIL_CODE_TIMEOUT, REGISTER_HEADLESS
from mail_provider import get_email_code

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "exa_apikeys.txt")
_SAVE_LOCK = threading.Lock()
_ACCOUNT_PASSWORD_LABEL = "EMAIL_OTP_ONLY"
_EXA_AUTH_URL = "https://auth.exa.ai/?callbackUrl=https%3A%2F%2Fdashboard.exa.ai%2F"


class EmailDomainBannedError(RuntimeError):
    pass
_EXA_HOME_URL = "https://dashboard.exa.ai/home"
_EXA_WARMUP_URLS = [
    "https://exa.ai/",
    "https://exa.ai/docs/reference/search-api-guide",
]


def fill_first_input(page, selectors, value):
    for selector in selectors:
        if page.query_selector(selector):
            page.fill(selector, value)
            return selector
    return None


def _move_mouse_to_element(page, element):
    try:
        box = element.bounding_box()
        if not box:
            return False
        target_x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
        target_y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        start_x = max(20, target_x + random.uniform(-160, 160))
        start_y = max(20, target_y + random.uniform(-120, 120))
        page.mouse.move(start_x, start_y)
        time.sleep(random.uniform(0.08, 0.18))
        page.mouse.move(target_x, target_y, steps=random.randint(12, 28))
        return True
    except Exception:
        return False


def _idle_mouse_jitter(page):
    try:
        viewport = page.viewport_size or {"width": 1366, "height": 900}
        width = max(400, int(viewport.get("width", 1366)))
        height = max(300, int(viewport.get("height", 900)))
        x1 = random.randint(int(width * 0.2), int(width * 0.8))
        y1 = random.randint(int(height * 0.15), int(height * 0.75))
        x2 = min(width - 10, max(10, x1 + random.randint(-120, 120)))
        y2 = min(height - 10, max(10, y1 + random.randint(-90, 90)))
        page.mouse.move(x1, y1, steps=random.randint(8, 18))
        time.sleep(random.uniform(0.12, 0.35))
        page.mouse.move(x2, y2, steps=random.randint(6, 16))
        return True
    except Exception:
        return False


def click_first(page, selectors):
    for selector in selectors:
        element = page.query_selector(selector)
        if not element:
            continue
        try:
            _move_mouse_to_element(page, element)
            time.sleep(random.uniform(0.12, 0.35))
            element.click(no_wait_after=True)
            return True
        except Exception:
            try:
                page.click(selector, no_wait_after=True)
                return True
            except Exception:
                continue
    return False


def human_type_first_input(page, selectors, value):
    for selector in selectors:
        element = page.query_selector(selector)
        if not element:
            continue
        try:
            _move_mouse_to_element(page, element)
        except Exception:
            pass
        try:
            element.click()
        except Exception:
            try:
                page.click(selector)
            except Exception:
                continue
        time.sleep(random.uniform(0.2, 0.6))
        try:
            element.fill("")
        except Exception:
            try:
                page.fill(selector, "")
            except Exception:
                pass
        for ch in value:
            try:
                element.type(ch, delay=random.randint(60, 180))
            except Exception:
                try:
                    page.keyboard.type(ch, delay=random.randint(60, 180))
                except Exception:
                    return None
            if random.random() < 0.12:
                time.sleep(random.uniform(0.08, 0.22))
        return selector
    return None


def extract_api_key(page):
    patterns = []
    try:
        text = page.locator("main").inner_text(timeout=3000)
        patterns.extend(re.findall(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", text, re.I))
    except Exception:
        pass
    try:
        html = page.content()
        patterns.extend(re.findall(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", html, re.I))
    except Exception:
        pass
    for candidate in patterns:
        return candidate
    return None


def _debug_dashboard_snapshot(page):
    try:
        text = page.locator("main").inner_text(timeout=3000)
    except Exception:
        try:
            text = page.inner_text("body", timeout=3000)
        except Exception:
            text = ""
    preview = re.sub(r"\s+", " ", text).strip()[:500]
    if preview:
        print(f"[debug] dashboard text preview: {preview}")
    else:
        print("[debug] dashboard text preview: <empty>")
    return preview


def _wait_for_browser_verification(page, timeout=25):
    start = time.time()
    while time.time() - start < timeout:
        preview = (_debug_dashboard_snapshot(page) or "").lower()
        blockers = (
            "we're verifying your browser",
            "checking your browser",
            "verify you are human",
            "just a moment",
            "enable javascript and cookies",
            "cloudflare",
        )
        if not any(token in preview for token in blockers):
            return True
        print("[debug] waiting for browser verification to clear")
        time.sleep(3)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
    return False


def fetch_api_key_via_dashboard_api(page):
    scripts = [
        """
        async () => {
            const response = await fetch('/api/get-api-keys', {
                method: 'GET', credentials: 'include', headers: { 'accept': 'application/json' },
            });
            return { status: response.status, body: await response.text() };
        }
        """,
        """
        async () => {
            const response = await fetch('https://dashboard.exa.ai/api/get-api-keys', {
                method: 'GET', credentials: 'include', headers: { 'accept': 'application/json' },
            });
            return { status: response.status, body: await response.text() };
        }
        """,
    ]
    saw_429 = False
    for idx, script in enumerate(scripts, 1):
        try:
            payload = page.evaluate(script)
        except Exception as exc:
            print(f"[debug] get-api-keys attempt#{idx} evaluate failed: {exc}")
            continue
        status = int(payload.get("status") or 0)
        if status == 429:
            saw_429 = True
        if status != 200:
            print(f"[debug] get-api-keys attempt#{idx} status={status} url={page.url}")
            continue
        try:
            data = json.loads(payload.get("body") or "{}")
        except Exception:
            continue
        for item in data.get("apiKeys", []):
            candidate = (item.get("id") or "").strip()
            if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", candidate, re.I):
                return {"key": candidate, "status": 200}
    return {"key": None, "status": 429 if saw_429 else 0}


def _safe_goto(page, url, timeout=30000, wait_until="domcontentloaded"):
    try:
        page.goto(url, wait_until=wait_until, timeout=timeout)
        return True
    except Exception as exc:
        if "NS_BINDING_ABORTED" in str(exc):
            print(f"[debug] safe_goto ignored NS_BINDING_ABORTED for {url}")
            return True
        print(f"[debug] safe_goto failed for {url}: {exc}")
        return False


def ensure_dashboard_ready(page):
    current_url = (page.url or "").lower()
    if "dashboard.exa.ai" not in current_url:
        try:
            page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
        except Exception:
            _safe_goto(page, _EXA_HOME_URL, timeout=30000)
            time.sleep(1.5)

    current_url = (page.url or "").lower()
    if "/onboarding" in current_url:
        click_first(page, ['button:text-is("Skip")'])
        time.sleep(1)
        click_first(page, ['button:text-is("Yes, I don\\\'t want the $10 in credits anyway!")', 'button:text-is("Yes")'])
        try:
            page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
        except Exception:
            pass
        time.sleep(2)

    current_url = (page.url or "").lower()
    if "/home" not in current_url:
        _safe_goto(page, _EXA_HOME_URL, timeout=20000)
        time.sleep(1.5)


def wait_for_api_key(page, timeout=20):
    start_time = time.time()
    attempt = 0
    api_rate_limited = False
    last_show_click = 0.0

    while time.time() - start_time < timeout:
        attempt += 1
        print(f"[debug] wait_for_api_key attempt#{attempt} url={page.url}")
        ensure_dashboard_ready(page)
        print(f"[debug] after ensure_dashboard_ready url={page.url}")

        if not _wait_for_browser_verification(page, timeout=20 if attempt == 1 else 10):
            print("[debug] browser verification still active")

        api_key = extract_api_key(page)
        if api_key:
            return api_key

        if time.time() - last_show_click > 2.5:
            clicked = click_first(page, [
                'button:text-is("Show")',
                'button:has-text("Show")',
                'button[aria-label*="show" i]',
                'button:has-text("API Key")',
                'button:has-text("Reveal")',
                'button:has-text("Display")',
            ])
            if clicked:
                print("[debug] clicked show api key")
                last_show_click = time.time()
                time.sleep(2)
                _debug_dashboard_snapshot(page)
                api_key = extract_api_key(page)
                if api_key:
                    return api_key
            elif attempt in {1, 3, 5}:
                _debug_dashboard_snapshot(page)

        should_try_api = (not api_rate_limited) or attempt == 1 or attempt in {4, 8}
        if should_try_api:
            result = fetch_api_key_via_dashboard_api(page)
            api_key = result.get("key")
            status = int(result.get("status") or 0)
            if api_key:
                return api_key
            if status == 429:
                api_rate_limited = True
                cooldown = min(6, max(2, timeout - (time.time() - start_time)))
                print(f"[debug] get-api-keys rate limited, cooldown {cooldown:.1f}s")
                time.sleep(cooldown)
                continue

        time.sleep(2)
        api_key = extract_api_key(page)
        if api_key:
            return api_key
    return None


def save_account(api_key):
    with _SAVE_LOCK:
        with open(_SAVE_FILE, "a", encoding="utf-8") as file_obj:
            file_obj.write(f"{api_key}\n")


def verify_api_key(api_key, timeout=30):
    try:
        response = std_requests.post(
            "https://api.exa.ai/search",
            json={"query": "api key verification", "numResults": 1},
            headers={"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=timeout,
        )
    except Exception as exc:
        print(f"❌ API Key 调用测试失败: {exc}")
        return False
    if response.status_code == 200:
        print("✅ API Key 调用测试通过")
        return True
    preview = response.text.strip().replace("\n", " ")[:160]
    print(f"❌ API Key 调用测试失败: HTTP {response.status_code}")
    if preview:
        print(f"   响应: {preview}")
    return False


def _apply_stealth(page):
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    headers = {
        "User-Agent": ua,
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Chromium";v="145", "Google Chrome";v="145", "Not A(Brand";v="99"',
        "sec-ch-ua-platform": '"macOS"',
        "sec-ch-ua-mobile": "?0",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        page.set_extra_http_headers(headers)
    except Exception:
        pass
    try:
        page.set_viewport_size({"width": 1366, "height": 900})
    except Exception:
        pass
    try:
        page.add_init_script(
            """
            (() => {
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            })();
            """
        )
    except Exception:
        pass


def _human_scroll_warmup(page, seconds=5):
    end_time = time.time() + seconds
    direction = 1
    while time.time() < end_time:
        amount = random.randint(180, 420) * direction
        try:
            page.mouse.wheel(0, amount)
        except Exception:
            try:
                page.evaluate("window.scrollBy(0, arguments[0])", amount)
            except Exception:
                pass
        time.sleep(random.uniform(0.4, 0.9))
        if random.random() < 0.25:
            direction *= -1


def _warmup_exa_session(page):
    try:
        page.goto(_EXA_WARMUP_URLS[0], wait_until="domcontentloaded", timeout=30000)
        time.sleep(random.uniform(1.8, 3.2))
        page.goto(_EXA_WARMUP_URLS[1], wait_until="domcontentloaded", timeout=45000)
        time.sleep(random.uniform(1.5, 2.5))
        scroll_seconds = random.uniform(3, 10)
        print(f"[debug] exa warmup scrolling for {scroll_seconds:.1f}s")
        _human_scroll_warmup(page, seconds=scroll_seconds)
        time.sleep(random.uniform(0.8, 1.6))
        print("[debug] exa warmup done")
    except Exception as exc:
        print(f"[debug] exa warmup skipped: {exc}")


def _launch_camoufox():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        result = {}
        def _run():
            result["browser"] = Camoufox(headless=REGISTER_HEADLESS)
            result["ctx"] = result["browser"].__enter__()
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join()
        class _Ctx:
            def __init__(self, browser, ctx):
                self.browser = browser
                self.ctx = ctx
            def __enter__(self):
                return self.ctx
            def __exit__(self, exc_type, exc, tb):
                return self.browser.__exit__(exc_type, exc, tb)
        return _Ctx(result.get("browser"), result.get("ctx"))
    return Camoufox(headless=REGISTER_HEADLESS)


def register_with_browser(email, password):
    print(f"🌐 使用浏览器模式注册 Exa: {email}")
    try:
        print(f"[debug] headless={REGISTER_HEADLESS}")
        with _launch_camoufox() as browser:
            print("[debug] browser launched")
            page = browser.new_page(user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
            print("[debug] new page created")
            _apply_stealth(page)
            try:
                page.set_extra_http_headers({"Referer": "https://exa.ai/", "Origin": "https://exa.ai"})
            except Exception:
                pass
            _warmup_exa_session(page)
            page.goto(_EXA_AUTH_URL, wait_until="networkidle", timeout=45000)
            print(f"[debug] page goto done: {page.url}")
            auth_idle = random.uniform(1.0, 3.0)
            print(f"[debug] auth idle before typing={auth_idle:.2f}s")
            time.sleep(auth_idle)
            _idle_mouse_jitter(page)
            time.sleep(random.uniform(0.2, 0.7))
            email_selector = human_type_first_input(page, ['input[type="email"]', 'input[placeholder="Email"]', 'input[aria-label="Email"]'], email)
            print(f"[debug] email_selector={email_selector}")
            if not email_selector:
                print("❌ Exa 登录页未找到邮箱输入框")
                return None
            pause_before_submit = random.uniform(0.8, 1.8)
            print(f"[debug] pause before submit={pause_before_submit:.2f}s")
            time.sleep(pause_before_submit)
            if not click_first(page, ['button:text-is("Continue")', 'button:has-text("Continue")', 'button:text-is("Continue with email")', 'button:has-text("Continue with email")', 'button:text-is("Verify")', 'button:has-text("Verify")', 'button[type="submit"]']):
                page.press(email_selector, "Enter")
                time.sleep(1 + random.random())
                if not click_first(page, ['button[type="submit"]']):
                    print("❌ Exa 登录页未找到 Continue/Submit 按钮")
                    return None
            print("[debug] clicked continue/verify")
            time.sleep(2)

            selectors = ['input[placeholder*="verification" i]', 'input[aria-label*="verification" i]', 'input[placeholder*="code" i]', 'input[aria-label*="code" i]', 'input[type="tel"]', 'input[name*="code" i]']
            start = time.time()
            code_selector = None
            code_page = page
            while time.time() - start < 120:
                for sel in selectors:
                    node = page.query_selector(sel)
                    if node:
                        code_selector = sel
                        code_page = page
                        break
                if not code_selector:
                    for frame in page.frames:
                        for sel in selectors:
                            node = frame.query_selector(sel)
                            if node:
                                code_selector = sel
                                code_page = frame
                                break
                        if code_selector:
                            break
                if code_selector:
                    break
                if int(time.time() - start) in {15, 30, 45, 60}:
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=15000)
                        print("[debug] reloaded verification page while waiting code input")
                    except Exception:
                        pass
                time.sleep(1)
            if not code_selector:
                print("❌ Exa 验证码页未出现输入框")
                raise EmailDomainBannedError("Exa 验证码页未出现输入框")
            print(f"[debug] code_selector={code_selector}")
            print("✅ 到达 Exa 邮箱验证码页")
            code = get_email_code(email, timeout=EMAIL_CODE_TIMEOUT, service="exa")
            print(f"[debug] received code={code}")
            if not code:
                print("❌ 未拿到验证码，放弃本轮")
                return None
            code_selector = fill_first_input(code_page, selectors, code)
            if not code_selector:
                print("❌ Exa 验证码页未找到输入框")
                return None
            if not click_first(code_page, ['button:text-is("VERIFY CODE")', 'button:text-is("Verify Code")', 'button:text-is("Verify")']):
                code_page.press(code_selector, "Enter")
            try:
                page.wait_for_url("**/dashboard.exa.ai/**", timeout=30000, wait_until="domcontentloaded")
            except Exception:
                if "accounts.google.com" in (page.url or "").lower():
                    print("⚠️ 检测到跳转 Google 登录，放弃本轮")
                    return None
                raise
            print(f"✅ Exa 登录成功: {page.url}")
            time.sleep(2)
            api_key = wait_for_api_key(page, timeout=API_KEY_TIMEOUT)
            if not api_key:
                print("⚠️  未找到 Exa API Key")
                return None
            print("🧪 验证 API Key 可用性...")
            if not verify_api_key(api_key):
                return None
            save_account(api_key)
            print("🎉 Exa 注册成功")
            print(f"   邮箱: {email}")
            print(f"   密码: {_ACCOUNT_PASSWORD_LABEL}")
            print(f"   Key : {api_key}")
            return api_key
    except EmailDomainBannedError:
        raise
    except Exception as exc:
        print(f"❌ Exa 注册失败: {exc}")
        traceback.print_exc()
        return None
