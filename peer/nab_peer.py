#!/usr/bin/env python3
"""nab-peer — seed account for the NoAccessBeGone name database.

Reads TOKEN/SHARE_KEY from env (injected by the supervisor).
  - polls the request queue and fulfills for guilds it is in
  - bulk-sweeps every guild it is in (immediately when membership changes)
  - with SCAN=1: join->scan->leave loop over public invites (residential IPs only)
"""
import json, os, re, subprocess, threading, time, urllib.parse, urllib.request, urllib.error

HUB = "https://nab.enby.fish"

POLL_EVERY = 120          # seconds between request-queue polls
BULK_EVERY = 12 * 3600    # seconds between full sweeps
FETCH_SPACING = 1.2       # seconds between discord api calls

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
                print(f"join blocked ({st}), backing off 10 min", flush=True)
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
            print(f"joined {gname} ({guild_id}) via {code}", flush=True)

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
                    print(f"scanned {gname}: {len(names)} channel names" +
                          (f" (uploaded {st3})" if st3 != 200 else ""), flush=True)
                else:
                    print(f"scanned {gname}: no channels", flush=True)
                time.sleep(FETCH_SPACING)

            joined.add(code)
            joined.add(guild_id)
            save()
            if was_member:
                print(f"already member of {gname}, not leaving", flush=True)
                time.sleep(interval)
                continue
            st4, _, retry = discord(f"/api/v9/users/@me/guilds/{guild_id}", token, method="DELETE")
            if st4 == 429:
                time.sleep(retry)
            elif st4 in (200, 204):
                print(f"left {gname}", flush=True)
            else:
                print(f"leave {gname} returned {st4}", flush=True)
        except Exception as e:
            print("scanner error:", e, flush=True)

        time.sleep(interval)


def load_config():
    """env vars win; fall back to a config file next to the script/binary."""
    env = {k: v for k, v in os.environ.items() if k in ("TOKEN", "SHARE_KEY", "SCAN", "SCAN_STATE", "DAILY_CAP", "JOIN_INTERVAL")}
    for path in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "peer.env"),
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
                            print(f"fulfilled {len(names)} names for guild {guild_id}", flush=True)
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
                            print(f"bulk: {len(names)} names from guild {guild_id}", flush=True)
                    time.sleep(FETCH_SPACING)
                print("bulk sweep done", flush=True)
                last_sweep = time.time()
                heartbeat()

        except Exception as e:
            print("error:", e, flush=True)

        time.sleep(POLL_EVERY)

if __name__ == "__main__":
    main()
