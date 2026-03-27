"""
Exa 注册统一入口（支持批量，按配置 DEFAULT_COUNT/DEFAULT_DELAY）
"""
import time
import random
import traceback

from config import DEFAULT_COUNT, DEFAULT_DELAY
from mail_provider import create_email, mark_banned_email
from exa_browser_solver import register_with_browser, EmailDomainBannedError


def register(email, password):
    """统一注册入口"""
    return register_with_browser(email, password)


def main():
    success = 0
    for idx in range(1, DEFAULT_COUNT + 1):
        print(f"\n=== 开始注册 {idx}/{DEFAULT_COUNT} ===")
        email = None
        try:
            email, password = create_email(service="exa")
            result = register(email, password)
            if result:
                success += 1
                print(f"✅ 注册成功: {email}")
            else:
                print(f"❌ 注册失败: {email}")
        except EmailDomainBannedError as exc:
            if email:
                mark_banned_email(email, str(exc))
            print(f"⚠️ 检测到 ban 邮箱/域名: {exc}")
            continue
        except Exception as exc:
            print(f"⚠️ 本轮异常: {exc}")
            traceback.print_exc()
            delay = random.uniform(15, 45)
            print(f"😴 随机休息 {delay:.1f}s 再继续")
            time.sleep(delay)
            continue

        if idx < DEFAULT_COUNT and DEFAULT_DELAY > 0:
            time.sleep(DEFAULT_DELAY)

    print(f"\n🎯 批量完成: 成功 {success}/{DEFAULT_COUNT}")


if __name__ == "__main__":
    main()
