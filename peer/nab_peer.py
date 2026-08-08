#!/usr/bin/env python3
"""nab-peer — seed account for the NoAccessBeGone name database.

Reads TOKEN/SHARE_KEY from env (injected by the supervisor).
  - polls the request queue and fulfills for guilds it is in
  - bulk-sweeps every guild it is in (immediately when membership changes)
  - with SCAN=1: join->scan->leave loop over public invites (residential IPs only)
"""
import json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request, urllib.error
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HUB = "https://nab.enby.fish"

POLL_EVERY = 120          # seconds between request-queue polls
BULK_EVERY = 12 * 3600    # seconds between full sweeps
FETCH_SPACING = 1.2       # seconds between discord api calls

events = deque(maxlen=200)
stats = {"joined": 0, "scanned": 0, "names": 0, "left": 0, "fulfilled": 0, "state": "starting"}
paused = False

def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    events.append({"t": time.strftime("%H:%M:%S"), "m": msg})

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

def http(url, data=None, headers=None, method=None):
    """curl-based request (urllib gets WAF-blocked on some endpoints)."""
    cmd = ["curl", "-sS", "-m", "30", "-L", "-w", "\n%{http_code}",
           "-A", UA]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if data is not None:
        cmd += ["--data-binary", data if isinstance(data, bytes) else json.dumps(data)]
    cmd.append(url)
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    body, _, code = out.rpartition("\n")
    try:
        code = int(code)
    except ValueError:
        return 0, body
    try:
        body = json.loads(body)
    except Exception:
        pass
    return code, body

def discord(url, token, method=None):
    st, body = http("https://discord.com" + url, headers={"authorization": token}, method=method)
    if isinstance(body, str):
        body = {}
    if st == 429:
        retry = 5
        try:
            retry = float(body.get("retry_after", 5)) if isinstance(body, dict) else 5
        except Exception:
            pass
        return st, None, retry
    if st != 200:
        return st, None, None
    return st, body, None


# ---------------- scanner: join public servers, scan, leave ----------------

SCAN_KEYWORDS = (os.environ.get("SCAN_KEYWORDS", "") or
    "minecraft server,gaming server,discord server,valorant,anime server,roblox server,music server,community server").split(",")

def ddg_invites(keyword):
    """Search DuckDuckGo HTML for discord.gg invite codes."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(f'"discord.gg/" {keyword}')
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return []
    codes = set(re.findall(r"discord\.gg(?:%2F|/)([A-Za-z0-9]{6,16})", html))
    return [c for c in codes if c != "servers"]

def scanner(token, key, state_path):
    """join -> scan -> leave loop. Only works from residential IPs."""
    joined = set()
    if os.path.isfile(state_path):
        try:
            with open(state_path) as f:
                joined = set(json.load(f).get("guilds", []))
        except Exception:
            pass
    daily_cap = int(os.environ.get("DAILY_CAP", "100"))
    interval = float(os.environ.get("JOIN_INTERVAL", "60"))
    today = time.strftime("%Y-%m-%d")
    count_today = 0
    ki = 0

    def save():
        try:
            with open(state_path, "w") as f:
                json.dump({"guilds": sorted(joined)}, f)
        except Exception:
            pass

    while True:
        try:
            if time.strftime("%Y-%m-%d") != today:
                today = time.strftime("%Y-%m-%d")
                count_today = 0
            if count_today >= daily_cap:
                time.sleep(600)
                continue

            keyword = SCAN_KEYWORDS[ki % len(SCAN_KEYWORDS)]
            ki += 1
            codes = [c for c in ddg_invites(keyword) if c not in joined]
            if not codes:
                time.sleep(interval)
                continue

            code = codes[0]
            st, body, retry = discord(f"/api/v9/invites/{code}", token)
            if st == 429:
                time.sleep(retry)
                continue
            if st in (401, 403, 1010):
                log(f"join blocked ({st}), backing off 10 min")
                time.sleep(600)
                continue
            if st != 200:
                joined.add(code)
                save()
                time.sleep(interval)
                continue

            guild = body.get("guild") or {}
            guild_id = str(guild.get("id", ""))
            gname = guild.get("name", "?")
            was_member = guild_id in joined
            if not guild_id:
                time.sleep(interval)
                continue

            count_today += 1
            log(f"joined {gname} ({guild_id}) via {code}")
            stats["joined"] += 1
            stats["state"] = "joined " + gname

            st2, channels, retry = discord(f"/api/v9/guilds/{guild_id}/channels", token)
            if st2 == 200:
                names = [{"c": c["id"], "n": c["name"]}
                         for c in channels
                         if isinstance(c.get("name"), str) and c["name"] != "___hidden___"]
                if names:
                    st3, _ = http(HUB + "/nab/upload", method="POST",
                                  data=json.dumps({"key": key, "batch": names,
                                                   "guild_id": guild_id, "guild_name": gname,
                                                   "invite": code}).encode(),
                                  headers={"content-type": "application/json"})
                    log(f"scanned {gname}: {len(names)} channel names" +
                          (f" (uploaded {st3})" if st3 != 200 else ""))
                    stats["scanned"] += 1
                    stats["names"] += len(names)
                    stats["state"] = "scanning"
                else:
                    log(f"scanned {gname}: no channels")
                stats["scanned"] += 1
                stats["state"] = "scanning"
                time.sleep(FETCH_SPACING)

            joined.add(code)
            joined.add(guild_id)
            save()
            if was_member:
                log(f"already member of {gname}, not leaving")
                time.sleep(interval)
                continue
            st4, _, retry = discord(f"/api/v9/users/@me/guilds/{guild_id}", token, method="DELETE")
            if st4 == 429:
                time.sleep(retry)
            elif st4 in (200, 204):
                log(f"left {gname}")
                stats["left"] += 1
                stats["state"] = "idle"
            else:
                log(f"leave {gname} returned {st4}")
        except Exception as e:
            log(f"scanner error: {e}")

        time.sleep(interval)


def load_config():
    """env vars win; fall back to a config file next to the script/binary."""
    env = {k: v for k, v in os.environ.items() if k in ("TOKEN", "SHARE_KEY", "SCAN", "SCAN_STATE", "DAILY_CAP", "JOIN_INTERVAL")}
    here = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(here, "peer.env"),
                 os.path.expanduser("~/.nab/peer.env")):
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        if k.strip() not in env:
                            env[k.strip()] = v.strip().strip('"').strip("'")
            except Exception:
                pass
    return env

def main():
    threading.Thread(target=start_ui, daemon=True).start()
    cfg = load_config()
    token = cfg.get("TOKEN", "")
    key = cfg.get("SHARE_KEY", "")
    if not token or not key:
        print("missing TOKEN/SHARE_KEY (env or peer.env next to this script)", flush=True)
        return

    if cfg.get("SCAN", "1") == "1":
        state = cfg.get("SCAN_STATE", os.path.expanduser("~/.nab/scanned.json"))
        threading.Thread(target=scanner, args=(token, key, state), daemon=True).start()

    my_guilds = {}
    last_sweep = 0  # sweep immediately on first loop, then every BULK_EVERY
    session_names = 0
    uid = cfg.get("ACCOUNT_UID", "")

    def heartbeat():
        if not uid:
            return
        try:
            http(HUB + "/nab/heartbeat", method="POST",
                 data=json.dumps({"key": key, "uid": uid, "names": session_names,
                                  "guilds": len(my_guilds)}).encode(),
                 headers={"content-type": "application/json"})
        except Exception:
            pass

    while True:
        try:
            # refresh guild membership list periodically; sweep soon if it changed
            if not my_guilds or time.time() % 3600 < POLL_EVERY:
                st, guilds, _ = discord("/api/v9/users/@me/guilds", token)
                if st == 200:
                    new_guilds = {g["id"]: g["name"] for g in guilds}
                    if set(new_guilds) != set(my_guilds):
                        last_sweep = 0
                    my_guilds = new_guilds

            # 1. fulfill request queue
            st, data = http(HUB + "/nab/requests", headers={"x-nab-key": key})
            if st == 200:
                for guild_id in data.get("guilds", []):
                    if guild_id not in my_guilds:
                        continue
                    st2, channels, retry = discord(f"/api/v9/guilds/{guild_id}/channels", token)
                    if st2 == 429:
                        time.sleep(retry)
                        continue
                    if st2 != 200:
                        continue
                    names = [{"c": c["id"], "n": c["name"]}
                             for c in channels
                             if isinstance(c.get("name"), str) and c["name"] != "___hidden___"]
                    if names:
                        st3, _ = http(HUB + "/nab/fulfill", method="POST",
                                      data=json.dumps({"key": key, "guild": guild_id, "names": names,
                                                       "guild_name": my_guilds.get(guild_id, "")}).encode(),
                                      headers={"content-type": "application/json"})
                        if st3 == 200:
                            log(f"fulfilled {len(names)} names for guild {guild_id}")
                        stats["fulfilled"] += 1
                    time.sleep(FETCH_SPACING)

            # 2. bulk sweep: push everything this account can see
            if time.time() - last_sweep >= BULK_EVERY:
                for guild_id in my_guilds:
                    st2, channels, retry = discord(f"/api/v9/guilds/{guild_id}/channels", token)
                    if st2 == 429:
                        time.sleep(retry)
                        continue
                    if st2 != 200:
                        continue
                    names = [{"c": c["id"], "n": c["name"]}
                             for c in channels
                             if isinstance(c.get("name"), str) and c["name"] != "___hidden___"]
                    if names:
                        st3, _ = http(HUB + "/nab/upload", method="POST",
                                      data=json.dumps({"key": key, "batch": names,
                                                       "guild_id": guild_id,
                                                       "guild_name": my_guilds.get(guild_id, "")}).encode(),
                                      headers={"content-type": "application/json"})
                        if st3 == 200:
                            session_names += len(names)
                            log(f"bulk: {len(names)} names from guild {guild_id}")
                            stats["names"] += len(names)
                            stats["scanned"] += 1
                    time.sleep(FETCH_SPACING)
                log("bulk sweep done")
                stats["state"] = "idle"
                last_sweep = time.time()
                heartbeat()

        except Exception as e:
            log(f"error: {e}")

        time.sleep(POLL_EVERY)

# ---------------- auto-register: create an account from this (home) IP ----------------

def register_account():
    """Register a fresh account locally (no phone). Returns the token or None."""
    import random, string
    addr = "".join(random.choices(string.ascii_lowercase + string.digits, k=12)) + "@reqbin.email"
    pw = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    print("creating temp email...", flush=True)
    st, body = http("https://api.mail.tm/accounts", method="POST",
                    data=json.dumps({"address": addr, "password": pw}).encode(),
                    headers={"content-type": "application/json"})
    if st != 201:
        print("mail.tm failed", flush=True)
        return None
    try:
        mtok = json.loads(body)["token"]
    except Exception:
        print("mail.tm no token", flush=True)
        return None

    print("solving captcha via nab relay...", flush=True)
    st, body = http(HUB + "/nab/captcha", method="POST", data=b"{}")
    if st != 200 or not isinstance(body, dict) or not body.get("ok"):
        print("captcha relay failed", flush=True)
        return None
    cap = body.get("token")

    print("registering with discord...", flush=True)
    st, body = http("https://discord.com/api/v9/experiments")
    fp = None
    if isinstance(body, str):
        m = re.search(r'"fingerprint":"([^"]+)"', body)
        fp = m.group(1) if m else None
    username = "nab" + "".join(random.choices(string.ascii_lowercase, k=7))
    payload = {"consent": True, "date_of_birth": "2000-01-01", "email": addr,
               "fingerprint": fp, "username": username, "password": pw,
               "captcha_key": cap, "invite": None, "gift_code_sku_id": None}
    st, body = http("https://discord.com/api/v9/auth/register", method="POST",
                    data=json.dumps(payload).encode(),
                    headers={"content-type": "application/json", "origin": "https://discord.com",
                             "x-super-properties": "e30="})
    if isinstance(body, str):
        print("register response:", body[:200], flush=True)
        return None
    token = body.get("token")
    if not token:
        print("register failed:", json.dumps(body)[:200], flush=True)
        if body.get("phone_verify_required"):
            print("account needs phone verification at registration — use a manual token instead", flush=True)
        return None

    try:
        st, me = http("https://discord.com/api/v9/users/@me", headers={"authorization": token})
        uid = me.get("id", "") if isinstance(me, dict) else ""
    except Exception:
        uid = ""
    if uid:
        print(f"registered {username} ({uid})", flush=True)
    return token



# ---------------- local dashboard (http://localhost:8092) ----------------

UI_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>nab peer</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:dark}body{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#0b0e14;color:#c9d1d9;display:flex;flex-direction:column;align-items:center;min-height:100vh}
main{max-width:820px;width:100%;padding:2rem 1.5rem}
h1{font-size:1.4rem;letter-spacing:.12em;color:#e6edf3}h1 span{color:#58a6ff}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:.7rem;margin:1.2rem 0}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:.9rem}
.card .n{font-size:1.4rem;font-weight:700;color:#58a6ff}.card .l{font-size:.7rem;color:#8b949e;text-transform:uppercase;letter-spacing:.08em;margin-top:.3rem}
#state{color:#3fb950;font-size:.85rem;margin-bottom:.5rem}
#state.busy{color:#d29922}#state.err{color:#f85149}
#log{background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:1rem;height:340px;overflow-y:auto;font-size:.8rem;line-height:1.6}
#log div{border-bottom:1px solid #161b22}.t{color:#8b949e;margin-right:.6rem}
button{background:#238636;border:none;color:#fff;border-radius:8px;padding:.5rem 1.1rem;font-family:inherit;cursor:pointer;margin-top:1rem}
button.off{background:#da3633}
</style></head><body><main>
<h1>nab<span>.</span> peer</h1>
<div id="state">connecting…</div>
<div class="cards">
<div class="card"><div class="n" id="joined">0</div><div class="l">joined</div></div>
<div class="card"><div class="n" id="scanned">0</div><div class="l">servers scanned</div></div>
<div class="card"><div class="n" id="names">0</div><div class="l">names uploaded</div></div>
<div class="card"><div class="n" id="fulfilled">0</div><div class="l">requests fulfilled</div></div>
<div class="card"><div class="n" id="left">0</div><div class="l">left</div></div>
</div>
<div id="log"></div>
<button id="pause">pause</button>
<script>
const el=id=>document.getElementById(id);
async function tick(){try{
const r=await fetch('/state');const d=await r.json();
el('joined').textContent=d.joined;el('scanned').textContent=d.scanned;el('names').textContent=d.names;
el('fulfilled').textContent=d.fulfilled;el('left').textContent=d.left;
const s=el('state');s.textContent=(d.paused?'PAUSED — ':'')+d.state;
s.className=d.paused?'err':(d.state==='idle'?'':'busy');
const lg=el('log');let first=lg.children.length===0;
lg.innerHTML='';d.events.forEach(e=>{const x=document.createElement('div');x.innerHTML='<span class=t>'+e.t+'</span>'+e.m;lg.appendChild(x)});
if(first)lg.scrollTop=lg.scrollHeight;
}catch(e){el('state').textContent='ui error: '+e}}
setInterval(tick,2000);tick();
el('pause').onclick=async()=>{const r=await fetch('/action',{method:'POST'});const d=await r.json();el('pause').textContent=d.paused?'resume':'pause';el('pause').className=d.paused?'off':''};
</script>
</main></body></html>"""

class UIHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if self.path == "/state":
            body = json.dumps({"joined": stats["joined"], "scanned": stats["scanned"],
                               "names": stats["names"], "fulfilled": stats["fulfilled"],
                               "left": stats["left"], "state": stats["state"],
                               "paused": paused, "events": list(events)}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        body = UI_PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        global paused
        if self.path == "/action":
            paused = not paused
            log(("paused" if paused else "resumed") + " via web ui")
            body = json.dumps({"paused": paused}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

def start_ui():
    port = int(os.environ.get("UI_PORT", "8092"))
    try:
        ThreadingHTTPServer(("127.0.0.1", port), UIHandler).serve_forever()
    except Exception as e:
        log(f"ui failed to start on :{port}: {e}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--register":
        token = register_account()
        if not token:
            sys.exit(1)
        conf = os.path.expanduser("~/.nab/peer.env")
        os.makedirs(os.path.dirname(conf), exist_ok=True)
        key = os.environ.get("SHARE_KEY", "a436975c7eb45eadac09659e4dce92f9f2207c8be40bfadc")
        with open(conf, "w") as f:
            f.write(f"TOKEN={token}\nSHARE_KEY={key}\nSCAN=1\nDAILY_CAP=100\nJOIN_INTERVAL=60\n")
        os.chmod(conf, 0o600)
        print(f"config written to {conf}")
        sys.exit(0)
    main()
