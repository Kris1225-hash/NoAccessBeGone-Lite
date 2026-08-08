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
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

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

# ---------- 5sim ----------
def sms_buy(key, country, operator):
    st, body = curl(f"https://5sim.net/v1/user/buy/activation/{country}/{operator}/discord",
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    if st != 200:
        return None, None
    d = json.loads(body)
    return d.get("id"), d.get("phone")

def sms_wait(key, oid, timeout=300):
    end = time.time() + timeout
    while time.time() < end:
        st, body = curl(f"https://5sim.net/v1/user/check/{oid}",
                        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        if st == 200:
            d = json.loads(body)
            sms = d.get("sms") or []
            if sms:
                return sms[0].get("code") or re.search(r"\d{6}", sms[0].get("text", "")).group(0)
        time.sleep(5)
    return None

def sms_finish(key, oid):
    curl(f"https://5sim.net/v1/user/finish/{oid}",
         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})

def sms_cancel(key, oid):
    curl(f"https://5sim.net/v1/user/cancel/{oid}",
         headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})

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
    five = env.get("FIVE_SIM_KEY", "")
    two = env.get("TWO_CAPTCHA_KEY", "")
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
                oid, phone = sms_buy(five, country, operator)
                if not phone:
                    log("  5sim buy failed")
                    time.sleep(10)
                    continue
                log(f"  rented {phone}")
                code = sms_wait(five, oid)
                if not code:
                    log("  no sms, cancelling")
                    sms_cancel(five, oid)
                    continue
                st2 = discord_phone_verify(phone, code, proxy)
                log(f"  phone verify -> {st2}")
                sms_finish(five, oid)
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
