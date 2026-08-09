#!/usr/bin/env python3
"""nab-factory — mass-produce verified Discord accounts for the name database.

Flow per account: temp email (mail.tm) -> hcaptcha solve (2captcha) -> register
(via proxy) -> phone verify (5sim) -> token -> accounts.env on the vps.

Config via /var/lib/private/nab-factory/.env:
  FIVE_SIM_KEY=...     5sim.net API token
  TWO_CAPTCHA_KEY=...  2captcha API key
  COUNT=20             accounts to create
  COUNTRY=any          number country (e.g. any, russia, indonesia)
  OPERATOR=any
  PROXIES=one:two      http proxies, one used per account (rotated)
  PROXY_AUTH=user:pass
"""
import json, os, random, re, string, subprocess, sys, time, urllib.parse

STATE = "/var/lib/private/nab-factory"
ENV_PATH = os.path.join(STATE, ".env")
ACCOUNTS_ENV = "/var/lib/private/nab/accounts.env"

def candidate_env_paths():
    here = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    return [os.path.join(here, "factory.env"),
            os.path.expanduser("~/.nab/factory.env"),
            ENV_PATH]
LOG = "/var/log/nab/factory.log"
HUB = "https://nab.enby.fish"

DISCORD_SITEKEY = "f5561ba9-8f1e-40ca-9b5b-a0b3f7192c34"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9210 Chrome/134.0.6998.205 Electron/35.3.0 Safari/537.36"

import uuid as _uuid

def super_properties():
    props = {
        "os": "Windows", "browser": "Discord Client", "device": "",
        "system_locale": "en-US", "browser_user_agent": UA,
        "browser_version": "134.0.6998.205", "os_version": "10.0.26100",
        "os_arch": "x64", "app_arch": "x64", "os_sdk_version": "26100",
        "referrer": "", "referring_domain": "", "referrer_current": "",
        "referring_domain_current": "", "release_channel": "stable",
        "client_build_number": 589596, "client_event_source": None,
        "client_launch_id": str(_uuid.uuid4()), "launch_signature": str(_uuid.uuid4()),
        "client_heartbeat_session_id": str(_uuid.uuid4()), "client_app_state": "focused"
    }
    return __import__("base64").b64encode(json.dumps(props).encode()).decode()

def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def load_env():
    env = {}
    for path in candidate_env_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass
    return env

def curl(url, method="GET", headers=None, data=None, proxy=None, timeout=40):
    cmd = ["curl", "-sS", "-m", str(timeout), "-L", "-w", "\n%{http_code}",
           "-A", UA]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if proxy:
        cmd += ["-x", proxy]
    if "x-super-properties" not in str(headers or {}):
        cmd += ["-H", "x-super-properties: " + super_properties()]
    if data is not None:
        if isinstance(data, dict):
            data = json.dumps(data)
        cmd += ["--data-binary", data]
    cmd.append(url)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True).stdout
        body, _, code = out.rpartition("\n")
        try:
            code = int(code)
        except ValueError:
            return 0, out
        return code, body
    except Exception as e:
        return 0, str(e)

# ---------- mail.tm ----------
def mail_create():
    addr = "".join(random.choices(string.ascii_lowercase + string.digits, k=12)) + "@reqbin.email"
    pw = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    st, body = curl("https://api.mail.tm/accounts", method="POST",
                    data={"address": addr, "password": pw})
    if st != 201:
        return None, None, None
    try:
        tok = json.loads(body)["token"]
    except Exception:
        return None, None, None
    return addr, pw, tok

def mail_wait_code(tok, timeout=180):
    """Wait for the discord verification email and return the link/code."""
    end = time.time() + timeout
    while time.time() < end:
        st, body = curl("https://api.mail.tm/messages",
                        headers={"Authorization": f"Bearer {tok}"})
        if st == 200:
            try:
                msgs = json.loads(body).get("hydra:member", [])
                for m in msgs:
                    subject = (m.get("subject") or "").lower()
                    if "verify" in subject or "discord" in subject:
                        text = (m.get("text") or "") + json.dumps(m.get("html") or "")
                        m2 = re.search(r"https://discord\.com/register[\w?&=./-]*", text)
                        if m2:
                            return m2.group(0)
            except Exception:
                pass
        time.sleep(10)
    return None

# ---------- 2captcha ----------
def captcha_solve(key):
    st, body = curl("https://2captcha.com/in.php",
                    params=None,
                    data=f"key={key}&method=hcaptcha&sitekey={DISCORD_SITEKEY}"
                         f"&pageurl=https%3A%2F%2Fdiscord.com%2Fregister",
                    method="POST")
    if not body.startswith("OK|"):
        return None
    cid = body[3:]
    for _ in range(60):
        time.sleep(5)
        st, body = curl("https://2captcha.com/res.php",
                        data=f"key={key}&action=get&id={cid}")
        if body.startswith("OK|"):
            return body[3:]
        if body == "CAPCHA_NOT_READY":
            continue
        return None
    return None

# ---------- textverified ----------
TV_BASE = "https://www.textverified.com/api/pub/v2"

def tv_token(key, username):
    st, body = curl(f"{TV_BASE}/auth", method="POST",
                    headers={"X-API-KEY": key, "X-API-USERNAME": username,
                             "content-type": "application/json"},
                    data={})
    try:
        return json.loads(body).get("token")
    except Exception:
        return None

def tv_create_verification(key, username, max_price=2.0):
    tok = tv_token(key, username)
    if not tok:
        return None, None
    # capture the Location header via curl -i
    import subprocess as sp
    cmd = ["curl", "-sS", "-m", "30", "-D", "-", "-o", "/tmp/tv_body.json",
           "-X", "POST", f"{TV_BASE}/verifications",
           "-H", f"Authorization: Bearer {tok}", "-H", "content-type: application/json",
           "--data-binary", json.dumps({"serviceName": "discord", "capability": "Sms",
                                        "maxPrice": max_price})]
    out = sp.run(cmd, capture_output=True, text=True).stdout
    loc = None
    for line in out.splitlines():
        if line.lower().startswith("location:"):
            loc = line.split(":", 1)[1].strip()
    try:
        d = json.load(open("/tmp/tv_body.json"))
        number = d.get("phoneNumber") or d.get("phone") or ""
    except Exception:
        number = ""
    return loc, number

def tv_wait_code(loc, key, username, timeout=300):
    """GET location -> {number, sms:{href}} -> GET sms href -> code"""
    tok = tv_token(key, username)
    end = time.time() + timeout
    while time.time() < end:
        st, body = curl(loc, headers={"Authorization": f"Bearer {tok}"})
        try:
            d = json.loads(body)
        except Exception:
            time.sleep(5)
            continue
        number = d.get("number") or ""
        sms_link = (d.get("sms") or {}).get("href") if isinstance(d.get("sms"), dict) else None
        if sms_link:
            if not sms_link.startswith("http"):
                sms_link = "https://www.textverified.com" + sms_link
            st2, body2 = curl(sms_link, headers={"Authorization": f"Bearer {tok}"})
            try:
                d2 = json.loads(body2)
                code = d2.get("code") or d2.get("smsCode") or ""
                if not code:
                    m = re.search(r"\b\d{4,8}\b", str(d2))
                    code = m.group(0) if m else ""
                if code:
                    return number, code
            except Exception:
                pass
        time.sleep(5)
    return number, None

# ---------- discord ----------
def discord_fingerprint(proxy):
    st, body = curl("https://discord.com/api/v9/experiments", proxy=proxy)
    if st != 200:
        return None
    m = re.search(r'"fingerprint":"([^"]+)"', body)
    return m.group(1) if m else None

def discord_register(email, password, username, fingerprint, captcha, proxy):
    payload = {
        "consent": True,
        "date_of_birth": "2000-01-01",
        "email": email,
        "fingerprint": fingerprint,
        "username": username,
        "password": password,
        "captcha_key": captcha,
        "invite": None,
        "gift_code_sku_id": None,
    }
    st, body = curl("https://discord.com/api/v9/auth/register", method="POST",
                    headers={"content-type": "application/json", "origin": "https://discord.com",
                             "x-super-properties": "e30="},
                    data=payload, proxy=proxy)
    try:
        d = json.loads(body)
    except Exception:
        return st, {}
    return st, d

def discord_phone_start(phone, proxy):
    st, body = curl("https://discord.com/api/v9/phone-verifications/start", method="POST",
                    headers={"content-type": "application/json", "origin": "https://discord.com",
                             "x-super-properties": "e30="},
                    data={"phone": phone, "register_fingerprint": None}, proxy=proxy)
    return st

def discord_phone_verify(phone, code, proxy):
    st, body = curl("https://discord.com/api/v9/phone-verifications/verify", method="POST",
                    headers={"content-type": "application/json", "origin": "https://discord.com",
                             "x-super-properties": "e30="},
                    data={"phone": phone, "token": code, "register_fingerprint": None}, proxy=proxy)
    return st

def discord_login(email, password, proxy):
    st, body = curl("https://discord.com/api/v9/auth/login", method="POST",
                    headers={"content-type": "application/json", "origin": "https://discord.com",
                             "x-super-properties": "e30="},
                    data={"login": email, "password": password}, proxy=proxy)
    try:
        d = json.loads(body)
        return d.get("token")
    except Exception:
        return None

def set_profile(token, proxy):
    """Set display name + bio for guerrilla advertising."""
    name = os.environ.get("PROFILE_NAME", "nab.enby.fish")
    bio = os.environ.get("PROFILE_BIO", "hidden channel name database — nab.enby.fish")
    st, _ = curl("https://discord.com/api/v9/users/@me", method="PATCH",
                 headers={"content-type": "application/json", "authorization": token,
                          "origin": "https://discord.com", "x-super-properties": "e30="},
                 data={"global_name": name}, proxy=proxy)
    st2, _ = curl("https://discord.com/api/v9/user_profile", method="PATCH",
                  headers={"content-type": "application/json", "authorization": token,
                           "origin": "https://discord.com", "x-super-properties": "e30="},
                  data={"bio": bio}, proxy=proxy)
    return st, st2

def main():
    env = load_env()
    five = env.get("TV_API_KEY", "")
    two = env.get("TV_API_USERNAME", "")
    count = int(env.get("COUNT", "5"))
    country = env.get("COUNTRY", "any")
    operator = env.get("OPERATOR", "any")
    proxies = [p for p in env.get("PROXIES", "").split(",") if p]
    if not five or not two:
        log("missing FIVE_SIM_KEY / TWO_CAPTCHA_KEY in " + ENV_PATH)
        return

    made = 0
    for i in range(count):
        proxy = proxies[i % len(proxies)] if proxies else None
        log(f"[{i+1}/{count}] starting (proxy: {proxy or 'none'})")
        try:
            email, pw, mtok = mail_create()
            if not email:
                log("  mail.tm failed")
                time.sleep(10)
                continue

            cap = captcha_solve(two)
            if not cap:
                log("  captcha solve failed")
                time.sleep(10)
                continue

            fp = discord_fingerprint(proxy)
            if not fp:
                log("  fingerprint failed")
                time.sleep(10)
                continue

            username = "user" + "".join(random.choices(string.ascii_lowercase, k=8))
            st, reg = discord_register(email, pw, username, fp, cap, proxy)
            token = reg.get("token")
            if not token:
                log(f"  register -> {st}: {str(reg)[:150]}")
                if st == 200 and reg.get("phone_verify_required"):
                    token = reg.get("token")
                else:
                    time.sleep(10)
                    continue

            # phone wall?
            st2 = None
            if not token or "phone" in str(reg).lower() or reg.get("captcha_required") is not None:
                loc, phone = tv_create_verification(five, two)
                if not loc:
                    log("  textverified create failed")
                    time.sleep(10)
                    continue
                log(f"  verification created")
                phone, code = tv_wait_code(loc, five, two)
                if not code:
                    log("  no sms received")
                    continue
                if phone:
                    st2 = discord_phone_verify(phone, code, proxy)
                    log(f"  phone verify -> {st2}")
                if not token:
                    token = discord_login(email, pw, proxy)

            if not token:
                log("  no token")
                time.sleep(10)
                continue

            st3, body = curl("https://discord.com/api/v9/users/@me",
                             headers={"authorization": token})
            try:
                uid = json.loads(body).get("id", "")
            except Exception:
                uid = ""
            if not uid:
                log("  token dead on arrival")
                time.sleep(10)
                continue

            if os.environ.get("SET_PROFILE", "1") == "1":
                sp, sp2 = set_profile(token, proxy)
                log(f"  profile set ({sp}/{sp2})")

            with open(ACCOUNTS_ENV, "a") as f:
                f.write(f"ACCOUNT_{uid}={token}\n")
            log(f"  ACCOUNT {uid} registered + added")
            made += 1
        except Exception as e:
            log(f"  error: {e}")
        time.sleep(random.uniform(8, 20))

    log(f"done: {made}/{count} accounts")

if __name__ == "__main__":
    main()
