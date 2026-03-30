# Grok (x.ai) 注册机使用教程

## 环境准备

1. 安装依赖并创建环境（Python 3.10+）：

```bash
cd grok-register
uv sync
```

2. 准备输出目录：

```bash
mkdir -p keys
```

3. 配置 YesCaptcha（必需）：在 `.env` 写入

```env
YESCAPTCHA_KEY="你的_yescaptcha_key"
```

4. 如需使用 LuckMail，额外配置：

```env
EMAIL_PROVIDER="luckmail"
LUCKMAIL_BASE_URL="https://mails.luckyous.com"
LUCKMAIL_API_KEY="你的_luckmail_api_key"
# 可选
LUCKMAIL_API_SECRET="你的_luckmail_api_secret"
LUCKMAIL_USE_HMAC="false"
LUCKMAIL_PROJECT_CODE="grok"
LUCKMAIL_EMAIL_TYPE="ms_graph"
LUCKMAIL_DOMAIN="outlook.com"
```

5. 如需代理，编辑 `grok.py` 顶部的 `PROXIES`。

## 运行示例

默认使用 `gptmail`：

```bash
cd grok-register
uv run python grok.py
# 提示输入并发数，回车默认 8
```

显式使用 `luckmail`：

```bash
cd grok-register
uv run python grok.py --email-provider luckmail
```

也可以直接指定线程数：

```bash
uv run python grok.py --email-provider luckmail --threads 8
```

## 参数说明

- `--email-provider`：邮箱提供商，可选 `gptmail` / `luckmail`。默认读取 `EMAIL_PROVIDER`，未设置时为 `gptmail`。
- `--threads`：并发线程数；不传则启动时交互输入，默认 8。
- `LUCKMAIL_BASE_URL`：LuckMail 平台地址，默认可用值为 `https://mails.luckyous.com`。
- `LUCKMAIL_API_KEY`：LuckMail API Key，使用 `luckmail` 时必填。
- `LUCKMAIL_API_SECRET` / `LUCKMAIL_USE_HMAC`：LuckMail 可选 HMAC 鉴权配置。
- `LUCKMAIL_PROJECT_CODE` / `LUCKMAIL_EMAIL_TYPE` / `LUCKMAIL_DOMAIN`：LuckMail 购买邮箱参数，默认分别为 `grok` / `ms_graph` / `outlook.com`。
- `YESCAPTCHA_KEY`：YesCaptcha 的 API Key，必填。

## 输出位置

成功后输出：

- `keys/grok.txt`：SSO token 列表
- `keys/accounts.txt`：`email:password:SSO`

## 注意

- 必须有 YesCaptcha 余额并配置 `YESCAPTCHA_KEY`。
- 若初始化提示“未找到 Action ID”，请更换代理或重试。
- `luckmail` 走的是**购买邮箱 + token 轮询邮件**的模式，不是一次性接码订单。
- 默认 provider 仍然是 `gptmail`，避免影响现有使用习惯。
