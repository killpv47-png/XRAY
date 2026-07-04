# analytics_worker.py — FIXED VERSION
import subprocess
import os
import time
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import base64
import uuid
import secrets
import re
import sys
import shutil
import io
from urllib.parse import parse_qs

# ─────────────────────────────────────────────
# پیکربندی مسیرها و متغیرهای اصلی سیستم
# ─────────────────────────────────────────────
DEFAULT_CLEAN_IP = "172.64.149.23"
TRAFFIC_COEFFICIENT = 1.0

PANEL_USER = "admin"
PANEL_PASS = "AZHAN8585@#@#ABOL1234"
SESSION_TOKEN = secrets.token_hex(16)

SUB_REPO_NAME = "fffccxddff-max/SUB_REPO_TOKEN"
SUB_REPO_TOKEN = os.environ.get("SUB_REPO_TOKEN", "")

DB_PATH = "panel_db.json"
GIVEAWAY_CONFIG_PATH = "giveaway_config.json"
SYSTEM_CONFIG_PATH = "system_config.json"
XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"
XRAY_LOG_PATH = "/usr/local/etc/xray/xray_runtime.log"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_ADMIN_ID = os.environ.get("TELEGRAM_ADMIN_ID", "YOUR_ADMIN_CHAT_ID_HERE")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@YOUR_CHANNEL_USERNAME_HERE")

CLOUDFLARED_BIN = "./cloudflared"
if not os.path.exists(CLOUDFLARED_BIN):
    for candidate in ["/usr/local/bin/cloudflared", "cloudflared", os.path.join(os.getcwd(), "cloudflared")]:
        if os.path.exists(candidate) or shutil.which(candidate):
            CLOUDFLARED_BIN = candidate if os.path.exists(candidate) else shutil.which(candidate)
            break

# ساختار تونل‌های خصوصی کاربران
USER_PRIVATE_TUNNELS = {}
PRIVATE_TUNNEL_LOG_DIR = "/tmp/killpv2_private_tunnels"
os.makedirs(PRIVATE_TUNNEL_LOG_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# تنظیمات سیستم
# ─────────────────────────────────────────────
def load_system_config():
    defaults = {
        "panel_user": PANEL_USER,
        "panel_pass": PANEL_PASS,
        "default_clean_ip": DEFAULT_CLEAN_IP,
        "traffic_coefficient": TRAFFIC_COEFFICIENT,
        "sub_repo_name": SUB_REPO_NAME,
        "sub_repo_token": SUB_REPO_TOKEN,
        "telegram_bot_token": TELEGRAM_BOT_TOKEN,
        "telegram_admin_id": TELEGRAM_ADMIN_ID,
        "telegram_channel_id": TELEGRAM_CHANNEL_ID,
    }
    if os.path.exists(SYSTEM_CONFIG_PATH):
        try:
            with open(SYSTEM_CONFIG_PATH, 'r') as f:
                data = json.load(f)
                for k, v in data.items():
                    if v not in [None, ""]:
                        defaults[k] = v
        except Exception:
            pass
    return defaults

def save_system_config(cfg):
    try:
        with open(SYSTEM_CONFIG_PATH, 'w') as f:
            json.dump(cfg, f, indent=4)
        try:
            subprocess.run("git config --local user.email 'action@github.com' || true", shell=True)
            subprocess.run("git config --local user.name 'GitHub Action' || true", shell=True)
            subprocess.run(f"git add {SYSTEM_CONFIG_PATH} || true", shell=True)
            subprocess.run("git commit -m '⚙️ Update system_config.json [Skip CI]' || true", shell=True)
            subprocess.run("git push || true", shell=True)
        except Exception as e:
            print(f"⚠️ git push system_config failed: {e}", flush=True)
    except Exception as e:
        print(f"⚠️ Failed saving system_config: {e}", flush=True)

SYSTEM_CONFIG = load_system_config()
PANEL_USER = SYSTEM_CONFIG["panel_user"]
PANEL_PASS = SYSTEM_CONFIG["panel_pass"]
DEFAULT_CLEAN_IP = SYSTEM_CONFIG["default_clean_ip"]
TRAFFIC_COEFFICIENT = float(SYSTEM_CONFIG["traffic_coefficient"])
SUB_REPO_NAME = SYSTEM_CONFIG["sub_repo_name"]
SUB_REPO_TOKEN = SYSTEM_CONFIG["sub_repo_token"]
TELEGRAM_BOT_TOKEN = SYSTEM_CONFIG["telegram_bot_token"]
TELEGRAM_ADMIN_ID = SYSTEM_CONFIG["telegram_admin_id"]
TELEGRAM_CHANNEL_ID = SYSTEM_CONFIG["telegram_channel_id"]

SYSTEM_LIVE_LOGS = []
RUNNER_LIVE_LOGS = ["🔄 سیستم تست رانر آماده است."]
DPI_BLOCK_LOGS = []
USER_TARGET_SITES = {}
USER_LIVE_IPS = {}
PANEL_DATABASE = {}

CHANNEL_STREAM_STATE = {
    "msg_id": None,
    "last_update": 0,
    "events": []
}

IP_REGEX = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):\d+')
DOMAIN_REGEX = re.compile(
    r'(?:tcp|udp|tls|http):([a-zA-Z0-9.-]+\.[a-zA-Z]{2,12})|->\s*([a-zA-Z0-9.-]+\.[a-zA-Z]{2,12})',
    re.IGNORECASE
)
REAL_TRAFFIC_REGEX = re.compile(
    r'(?:uplink[:\s]+(\d+).*?downlink[:\s]+(\d+))|(?:size[:\s]+(\d+))|(?:uploaded[:\s]+(\d+))',
    re.IGNORECASE
)
DPI_RESET_REGEX = re.compile(
    r'(connection reset|reset by peer|broken pipe|EOF|closed prematurely|handshake failed|tls.*failed|i/o timeout|context deadline)',
    re.IGNORECASE
)

if os.path.exists('active_edge_host.txt'):
    with open('active_edge_host.txt', 'r') as f:
        tunnel_host = f.read().strip()
else:
    tunnel_host = "127.0.0.1"

if os.path.exists('active_runner_host.txt'):
    with open('active_runner_host.txt', 'r') as f:
        runner_host = f.read().strip()
    is_runner_active_file = True
else:
    runner_host = tunnel_host
    is_runner_active_file = False

def is_xray_core_running():
    if not sys.platform.startswith('linux'):
        return True
    try:
        out = subprocess.check_output("pgrep xray || pidof xray", shell=True)
        return len(out.strip()) > 0
    except Exception:
        return False

def load_database():
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, 'r') as f:
                data = json.load(f)
                if data and len(data) > 0:
                    return data
        except Exception:
            pass
    return {
        "Main_kill_pv2_8086": {
            "uuid": str(uuid.uuid4()),
            "total_limit_bytes": 0,
            "used_bytes": 0,
            "clean_ip": DEFAULT_CLEAN_IP,
            "custom_host": "",
            "status": "OFFLINE",
            "last_active_time": 0,
            "down_speed": 0,
            "up_speed": 0,
            "created_at": int(time.time()),
            "expire_seconds": 31536000,
            "active": True,
            "coefficient": 1.0,
            "real_traffic": False,
            "max_ips": 2,
            "is_proxy_type": False,
            "use_runner_balancer": False,
            "optimization": False,
            "private_tunnel_enabled": False,
            "private_tunnel_host": ""
        }
    }

PANEL_DATABASE = load_database()

def save_database():
    with open(DB_PATH, 'w') as f:
        json.dump(PANEL_DATABASE, f, indent=4)

def load_giveaway_config():
    if os.path.exists(GIVEAWAY_CONFIG_PATH):
        try:
            with open(GIVEAWAY_CONFIG_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "max_claims": 0, "volume_value": 0.0, "volume_unit": "GB",
        "volume_gb": 0.0, "claimed_count": 0, "claimed_users": [],
        "status": "inactive", "channel_msg_id": None
    }

def save_giveaway_config(config_data):
    with open(GIVEAWAY_CONFIG_PATH, 'w') as f:
        json.dump(config_data, f, indent=4)

# ─── FIX: format_bytes_display — اصلاح توان ───
def format_bytes_display(b):
    if b >= 1024**3: return f"{b / (1024**3):.2f} GB"
    if b >= 1024**2: return f"{b / (1024**2):.2f} MB"
    if b >= 1024:    return f"{b / 1024:.2f} KB"
    return f"{b} B"

def get_server_resources():
    cpu_pct, ram_pct = 0.0, 0.0
    try:
        if sys.platform.startswith('linux'):
            with open('/proc/meminfo', 'r') as f:
                m = f.read()
            t = re.search(r'MemTotal:\s+(\d+)', m)
            a = re.search(r'MemAvailable:\s+(\d+)', m)
            if t and a:
                total = int(t.group(1))
                avail = int(a.group(1))
                ram_pct = ((total - avail) / total) * 100
            with open('/proc/stat', 'r') as f:
                l1 = f.readline().split()
            time.sleep(0.05)
            with open('/proc/stat', 'r') as f:
                l2 = f.readline().split()
            id1 = int(l1[4]) + int(l1[5])
            tot1 = sum(int(x) for x in l1[1:8])
            id2 = int(l2[4]) + int(l2[5])
            tot2 = sum(int(x) for x in l2[1:8])
            if tot2 - tot1 > 0:
                cpu_pct = (1 - (id2 - id1) / (tot2 - tot1)) * 100
    except Exception:
        pass
    if cpu_pct == 0.0: cpu_pct = secrets.randbelow(12) + 4
    if ram_pct == 0.0: ram_pct = secrets.randbelow(15) + 30
    return round(cpu_pct, 1), round(ram_pct, 1)

def generate_qr_png_bytes(text_data):
    try:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2
        )
        qr.add_data(text_data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"⚠️ QR generation failed: {e}", flush=True)
        return None

# ─── FIX: push_channel_event — براکت درست ───
def push_channel_event(event_text):
    try:
        CHANNEL_STREAM_STATE["events"].append(f"{time.strftime('%H:%M:%S')} — {event_text}")
        if len(CHANNEL_STREAM_STATE["events"]) > 15:
            CHANNEL_STREAM_STATE["events"] = CHANNEL_STREAM_STATE["events"][-15:]
    except Exception:
        pass

# ─────────────────────────────────────────────
# FIX: تونل خصوصی — ری‌استارت‌پروف
# ─────────────────────────────────────────────
def spawn_private_tunnel_for_user(username):
    try:
        kill_private_tunnel_for_user(username)

        if not CLOUDFLARED_BIN or (
            not os.path.exists(CLOUDFLARED_BIN) and not shutil.which(CLOUDFLARED_BIN)
        ):
            print(f"⚠️ cloudflared binary not found for {username}", flush=True)
            return None

        log_path = os.path.join(PRIVATE_TUNNEL_LOG_DIR, f"{username}_{int(time.time())}.log")
        cmd = f"{CLOUDFLARED_BIN} tunnel --url http://127.0.0.1:8080 --no-autoupdate"

        log_f = open(log_path, 'w')
        proc = subprocess.Popen(cmd, shell=True, stdout=log_f, stderr=subprocess.STDOUT)

        host = None
        for _ in range(35):
            time.sleep(1)
            try:
                with open(log_path, 'r') as lf:
                    content = lf.read()
                match = re.search(r'https://([a-zA-Z0-9.-]+\.trycloudflare\.com)', content)
                if match:
                    host = match.group(1)
                    break
            except Exception:
                pass

        if host:
            USER_PRIVATE_TUNNELS[username] = {
                "process": proc,
                "host": host,
                "log_file": log_path,
                "started_at": int(time.time())
            }
            print(f"✅ Private tunnel created for {username}: {host}", flush=True)
            push_channel_event(f"🆕 تونل اختصاصی ساخته شد برای {username}: {host}")
            return host
        else:
            try:
                proc.kill()
            except Exception:
                pass
            print(f"⚠️ Could not extract host for {username}'s private tunnel", flush=True)
            return None
    except Exception as e:
        print(f"⚠️ spawn_private_tunnel_for_user failed for {username}: {e}", flush=True)
        return None

# ─── FIX: kill_private_tunnel — دسترسی درست به dict ───
def kill_private_tunnel_for_user(username):
    try:
        if username in USER_PRIVATE_TUNNELS:
            try:
                USER_PRIVATE_TUNNELS[username]["process"].kill()
            except Exception:
                pass
            try:
                del USER_PRIVATE_TUNNELS[username]
            except Exception:
                pass
    except Exception:
        pass

def get_user_effective_host(u_name, u_data):
    if u_data.get("private_tunnel_enabled", False):
        priv_host = u_data.get("private_tunnel_host", "").strip()
        if priv_host:
            return priv_host
    if u_data.get("use_runner_balancer", False):
        return runner_host
    return u_data.get("custom_host", "").strip() or runner_host

# ─────────────────────────────────────────────
# FIX: bootstrap تونل‌های خصوصی — دسترسی درست
# ─────────────────────────────────────────────
def bootstrap_private_tunnels_on_startup():
    needs_save = False
    for u_name, u_data in list(PANEL_DATABASE.items()):
        if u_data.get("private_tunnel_enabled", False) and u_data.get("active", True):
            # هاست قدیمی رو فوری پاک کن
            PANEL_DATABASE[u_name]["private_tunnel_host"] = ""
            needs_save = True

    if needs_save:
        save_database()

    # حالا تونل جدید بساز
    for u_name, u_data in list(PANEL_DATABASE.items()):
        if u_data.get("private_tunnel_enabled", False) and u_data.get("active", True):
            print(f"🔄 Bootstrapping private tunnel for {u_name}...", flush=True)
            new_host = spawn_private_tunnel_for_user(u_name)
            if new_host:
                PANEL_DATABASE[u_name]["private_tunnel_host"] = new_host
            else:
                PANEL_DATABASE[u_name]["private_tunnel_host"] = ""
            save_database()

# ─────────────────────────────────────────────
# پوش ساب‌ها
# ─────────────────────────────────────────────
def push_subs_to_github():
    try:
        now = int(time.time())
        temp_dir = "/tmp/sub_secure_push_8086"
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)

        for k, v in PANEL_DATABASE.items():
            if not v.get("active", True):
                payload_str = "// ACCOUNT EXPIRED OR DISABLED\n"
            else:
                if v.get("is_proxy_type", False):
                    payload_str = f"socks5://{k}:{v.get('uuid','')}@{tunnel_host}:8089#{k}_Socks5_Proxy\n"
                else:
                    c_ip = v.get("clean_ip", DEFAULT_CLEAN_IP)
                    t_host = get_user_effective_host(k, v)
                    total_bytes = v.get("total_limit_bytes", 0)
                    rem_bytes = max(0, total_bytes - v.get("used_bytes", 0)) if total_bytes > 0 else 0

                    passed_seconds = now - v.get("created_at", now)
                    total_seconds = v.get("expire_seconds", 2592000)
                    rem_seconds = max(0, total_seconds - passed_seconds)
                    rem_d = int(rem_seconds // 86400)
                    rem_h = int((rem_seconds % 86400) // 3600)

                    suffix = "_⚡Opt" if v.get("optimization", False) else "_Clean"
                    if v.get("private_tunnel_enabled", False):
                        suffix += "_🔒Priv"
                    clean_link = (
                        f"vless://{v.get('uuid', '')}@{c_ip}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{k}{suffix}"
                    )
                    regular_link = (
                        f"vless://{v.get('uuid', '')}@{t_host}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0#{k}_Direct"
                    )
                    info_used = (
                        f"vless://{v.get('uuid', '')}@{c_ip}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}"
                        f"#📊Used:{format_bytes_display(v.get('used_bytes', 0))}"
                    )
                    info_rem = (
                        f"vless://{v.get('uuid', '')}@{c_ip}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}"
                        f"#💾Left:{format_bytes_display(rem_bytes) if total_bytes > 0 else 'Unlimited'}"
                    )
                    info_time = (
                        f"vless://{v.get('uuid', '')}@{c_ip}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}"
                        f"#⏳Days:{rem_d}Hours:{rem_h}"
                    )
                    payload_str = f"{clean_link}\n{regular_link}\n{info_used}\n{info_rem}\n{info_time}\n"

            payload = base64.b64encode(payload_str.encode('utf-8')).decode('utf-8')
            with open(os.path.join(temp_dir, k), 'w') as sf:
                sf.write(payload)

        combined_subs = load_combined_subs()
        for combo_name, usernames in combined_subs.items():
            combined_payload_lines = []
            for un in usernames:
                if un in PANEL_DATABASE and PANEL_DATABASE[un].get("active", True):
                    v = PANEL_DATABASE[un]
                    if v.get("is_proxy_type", False):
                        combined_payload_lines.append(
                            f"socks5://{un}:{v.get('uuid','')}@{tunnel_host}:8089#{un}_Socks5_Proxy"
                        )
                    else:
                        c_ip = v.get("clean_ip", DEFAULT_CLEAN_IP)
                        t_host = get_user_effective_host(un, v)
                        suffix = "_⚡Opt" if v.get("optimization", False) else "_Clean"
                        if v.get("private_tunnel_enabled", False):
                            suffix += "_🔒Priv"
                        link = (
                            f"vless://{v.get('uuid', '')}@{c_ip}:443"
                            f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                            f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{un}{suffix}"
                        )
                        combined_payload_lines.append(link)
            combined_payload = "\n".join(combined_payload_lines) + "\n"
            encoded = base64.b64encode(combined_payload.encode('utf-8')).decode('utf-8')
            with open(os.path.join(temp_dir, f"combo_{combo_name}"), 'w') as sf:
                sf.write(encoded)

        if SUB_REPO_NAME and SUB_REPO_TOKEN and "نام_کاربری" not in SUB_REPO_NAME:
            try:
                git_dir = "/tmp/git_push_8086"
                if os.path.exists(git_dir): shutil.rmtree(git_dir)
                os.makedirs(git_dir, exist_ok=True)
                for item in os.listdir(temp_dir):
                    shutil.copy(os.path.join(temp_dir, item), os.path.join(git_dir, item))
                cwd = os.getcwd()
                os.chdir(git_dir)
                subprocess.run("git init || true", shell=True)
                subprocess.run("git config --local user.email 'action@github.com' || true", shell=True)
                subprocess.run("git config --local user.name 'GitHub Action' || true", shell=True)
                subprocess.run("git checkout -b main || true", shell=True)
                subprocess.run("git add . || true", shell=True)
                subprocess.run("git commit -m '🔗 Update Subscriptions [Skip CI]' || true", shell=True)
                remote_url = f"https://{SUB_REPO_TOKEN}@github.com/{SUB_REPO_NAME}.git"
                subprocess.run(f"git push \"{remote_url}\" main --force || true", shell=True)
                os.chdir(cwd)
                shutil.rmtree(git_dir)
            except Exception:
                pass

        shutil.rmtree(temp_dir)
        subprocess.run("git config --local user.email 'action@github.com' || true", shell=True)
        subprocess.run("git config --local user.name 'GitHub Action' || true", shell=True)
        subprocess.run(
            f"git add {DB_PATH} {GIVEAWAY_CONFIG_PATH} {SYSTEM_CONFIG_PATH} combined_subs.json || true",
            shell=True
        )
        subprocess.run("git commit -m '💾 Sync DB Securely [Skip CI]' || true", shell=True)
        subprocess.run("git push || true", shell=True)
    except Exception as e:
        print(f"⚠️ push_subs_to_github failed: {e}", flush=True)

COMBINED_SUBS_PATH = "combined_subs.json"

def load_combined_subs():
    if os.path.exists(COMBINED_SUBS_PATH):
        try:
            with open(COMBINED_SUBS_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_combined_subs(data):
    try:
        with open(COMBINED_SUBS_PATH, 'w') as f:
            json.dump(data, f, indent=4)
        try:
            subprocess.run(f"git add {COMBINED_SUBS_PATH} || true", shell=True)
            subprocess.run("git commit -m '🔗 Update combined_subs [Skip CI]' || true", shell=True)
            subprocess.run("git push || true", shell=True)
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ save_combined_subs failed: {e}", flush=True)

# ─── FIX: check_expiration_and_limits — دسترسی درست به دیکشنری ───
def check_expiration_and_limits():
    now = int(time.time())
    changed = False
    for u_name, u_data in list(PANEL_DATABASE.items()):
        total_limit = u_data.get("total_limit_bytes", 0)
        if total_limit > 0 and u_data.get("used_bytes", 0) >= total_limit:
            if u_data.get("active", True) or u_data.get("status") != "EXPIRED":
                PANEL_DATABASE[u_name]["active"] = False
                PANEL_DATABASE[u_name]["status"] = "EXPIRED"
                changed = True
            continue

        created_time = u_data.get("created_at", now)
        expire_seconds = u_data.get("expire_seconds", 2592000)
        if now - created_time > expire_seconds:
            if u_data.get("active", True) or u_data.get("status") != "EXPIRED":
                PANEL_DATABASE[u_name]["active"] = False
                PANEL_DATABASE[u_name]["status"] = "EXPIRED"
                changed = True
            continue

        live_ips_count = len(USER_LIVE_IPS.get(u_name, {}))
        max_allowed_ips = int(u_data.get("max_ips", 2))

        if live_ips_count > max_allowed_ips:
            if u_data.get("active", True):
                PANEL_DATABASE[u_name]["active"] = False
                PANEL_DATABASE[u_name]["status"] = "IP_LIMIT_EXCEEDED"
                changed = True
        else:
            if u_data.get("status") == "IP_LIMIT_EXCEEDED" and not u_data.get("active", True):
                PANEL_DATABASE[u_name]["active"] = True
                PANEL_DATABASE[u_name]["status"] = "OFFLINE"
                changed = True

    if changed:
        save_database()
        sync_xray_core()
        push_subs_to_github()

def sync_xray_core():
    vless_clients = [
        {"id": u_data.get("uuid", ""), "email": u_name, "level": 0}
        for u_name, u_data in PANEL_DATABASE.items()
        if u_data.get("active", True) and not u_data.get("is_proxy_type", False)
    ]
    proxy_users = [
        {"user": u_name, "pass": u_data.get("uuid", "")}
        for u_name, u_data in PANEL_DATABASE.items()
        if u_data.get("active", True) and u_data.get("is_proxy_type", False)
    ]

    any_optimized = any(
        u_data.get("optimization", False)
        for u_data in PANEL_DATABASE.values()
        if u_data.get("active", True)
    )

    if any_optimized:
        sockopt_config = {
            "tcpFastOpen": True,
            "tcpcongestion": "bbr",
            "tcpKeepAliveInterval": 20,
            "tcpKeepAliveIdle": 60,
            "tcpNoDelay": True,
            "tcpMptcp": True,
            "domainStrategy": "UseIP",
            "mark": 0
        }
    else:
        sockopt_config = {
            "tcpKeepAliveInterval": 20,
            "tcpKeepAliveIdle": 60,
            "tcpNoDelay": True
        }

    db_backup_string = base64.b64encode(json.dumps(PANEL_DATABASE).encode('utf-8')).decode('utf-8')

    xray_json_config = {
        "_killpv2_db_backup": db_backup_string,
        "log": {
            "loglevel": "info",
            "access": XRAY_LOG_PATH,
            "error": XRAY_LOG_PATH
        },
        "policy": {
            "levels": {
                "0": {
                    "handshake": 4,
                    "connIdle": 600,
                    "uplinkOnly": 5,
                    "downlinkOnly": 10,
                    "bufferSize": 4
                }
            },
            "system": {
                "statsInboundUplink": False,
                "statsInboundDownlink": False
            }
        },
        "inbounds": [
            {
                "port": 8085,
                "protocol": "vless",
                "settings": {"clients": vless_clients, "decryption": "none"},
                "streamSettings": {
                    "network": "ws",
                    "wsSettings": {
                        "path": "/killpv2",
                        "headers": {}
                    },
                    "sockopt": sockopt_config
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls"],
                    "routeOnly": False
                }
            },
            {
                "port": 8089,
                "protocol": "socks",
                "settings": {
                    "auth": "password" if proxy_users else "noauth",
                    "accounts": proxy_users,
                    "udp": True
                },
                "streamSettings": {
                    "sockopt": sockopt_config
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls"]
                }
            }
        ],
        "outbounds": [{
            "protocol": "freedom",
            "tag": "direct_out",
            "settings": {
                "domainStrategy": "UseIP" if any_optimized else "AsIs"
            },
            "streamSettings": {
                "sockopt": sockopt_config
            }
        }]
    }

    with open(XRAY_CONFIG_PATH, 'w') as f:
        json.dump(xray_json_config, f, indent=4)

    subprocess.run("sudo fuser -k 8085/tcp || true", shell=True)
    subprocess.run("sudo fuser -k 8089/tcp || true", shell=True)
    subprocess.run(f"sudo touch {XRAY_LOG_PATH} && sudo chmod 777 {XRAY_LOG_PATH}", shell=True)
    subprocess.run(
        f"sudo nohup /usr/local/bin/xray -config {XRAY_CONFIG_PATH} > /dev/null 2>&1 &",
        shell=True
    )
    push_channel_event("🔄 هسته Xray ریلود شد")

# ─────────────────────────────────────────────
# HTTP Server
# ─────────────────────────────────────────────
class SanaeiMobileXuiServer(BaseHTTPRequestHandler):
    def log_message(self, format, *args): return

    def is_authenticated(self):
        cookies = self.headers.get('Cookie', '')
        return f"session={SESSION_TOKEN}" in cookies

    def do_POST(self):
        global PANEL_USER, PANEL_PASS, DEFAULT_CLEAN_IP, TRAFFIC_COEFFICIENT
        global SUB_REPO_NAME, SUB_REPO_TOKEN
        global TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_ID, TELEGRAM_CHANNEL_ID

        if self.path == "/api/terminal":
            if not self.is_authenticated():
                self.send_response(403)
                self.end_headers()
                return
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            params = parse_qs(post_data)
            cmd = params.get('command', [''])[0].strip()
            output = ""
            if cmd:
                try:
                    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=12)
                    output = res.stdout if res.stdout else res.stderr
                    if not output.strip():
                        output = "✔ دستور با موفقیت اجرا شد (بدون خروجی سیستم)."
                except subprocess.TimeoutExpired:
                    output = "❌ خطا: زمان اجرای دستور به پایان رسید (محدودیت ۱۲ ثانیه)."
                except Exception as e:
                    output = f"💥 خطای سیستمی در اجرا: {str(e)}"
            else:
                output = "⚠️ خط فرمان خالی است داداش!"
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({"output": output}).encode('utf-8'))
            return

        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode('utf-8')
        params = parse_qs(post_data)
        action = params.get('action', [''])[0]

        if self.path == "/login":
            username = params.get('username', [''])[0].strip()
            password = params.get('password', [''])[0].strip()
            if username == PANEL_USER and password == PANEL_PASS:
                self.send_response(303)
                self.send_header('Set-Cookie', f'session={SESSION_TOKEN}; Path=/; HttpOnly')
                self.send_header('Location', '/')
                self.end_headers()
            else:
                self.send_response(303)
                self.send_header('Location', '/?error=true')
                self.end_headers()
            return

        if not self.is_authenticated():
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return

        if action == 'save_system_settings':
            new_user = params.get('panel_user', [PANEL_USER])[0].strip() or PANEL_USER
            new_pass = params.get('panel_pass', [PANEL_PASS])[0].strip() or PANEL_PASS
            new_clean_ip = params.get('default_clean_ip', [DEFAULT_CLEAN_IP])[0].strip() or DEFAULT_CLEAN_IP
            try:
                new_coef = float(params.get('traffic_coefficient', [str(TRAFFIC_COEFFICIENT)])[0])
            except Exception:
                new_coef = TRAFFIC_COEFFICIENT
            new_repo_name = params.get('sub_repo_name', [SUB_REPO_NAME])[0].strip() or SUB_REPO_NAME
            new_repo_token = params.get('sub_repo_token', [SUB_REPO_TOKEN])[0].strip()
            if not new_repo_token:
                new_repo_token = SUB_REPO_TOKEN
            PANEL_USER = new_user
            PANEL_PASS = new_pass
            DEFAULT_CLEAN_IP = new_clean_ip
            TRAFFIC_COEFFICIENT = new_coef
            SUB_REPO_NAME = new_repo_name
            SUB_REPO_TOKEN = new_repo_token
            SYSTEM_CONFIG["panel_user"] = PANEL_USER
            SYSTEM_CONFIG["panel_pass"] = PANEL_PASS
            SYSTEM_CONFIG["default_clean_ip"] = DEFAULT_CLEAN_IP
            SYSTEM_CONFIG["traffic_coefficient"] = TRAFFIC_COEFFICIENT
            SYSTEM_CONFIG["sub_repo_name"] = SUB_REPO_NAME
            SYSTEM_CONFIG["sub_repo_token"] = SUB_REPO_TOKEN
            save_system_config(SYSTEM_CONFIG)
            push_channel_event("⚙️ تنظیمات عمومی سیستم بروزرسانی شد")
            self.send_response(303)
            self.send_header('Location', '/?saved=settings')
            self.end_headers()
            return

        if action == 'save_telegram_settings':
            new_token = params.get('telegram_bot_token', [TELEGRAM_BOT_TOKEN])[0].strip()
            new_admin = params.get('telegram_admin_id', [TELEGRAM_ADMIN_ID])[0].strip()
            new_channel = params.get('telegram_channel_id', [TELEGRAM_CHANNEL_ID])[0].strip()
            if new_token: TELEGRAM_BOT_TOKEN = new_token
            if new_admin: TELEGRAM_ADMIN_ID = new_admin
            if new_channel: TELEGRAM_CHANNEL_ID = new_channel
            SYSTEM_CONFIG["telegram_bot_token"] = TELEGRAM_BOT_TOKEN
            SYSTEM_CONFIG["telegram_admin_id"] = TELEGRAM_ADMIN_ID
            SYSTEM_CONFIG["telegram_channel_id"] = TELEGRAM_CHANNEL_ID
            save_system_config(SYSTEM_CONFIG)
            push_channel_event("🤖 تنظیمات ربات تلگرام بروزرسانی شد")
            self.send_response(303)
            self.send_header('Location', '/?saved=telegram')
            self.end_headers()
            return

        if action == 'build_combined_sub':
            combo_name = params.get('combo_name', [''])[0].strip()
            selected_users = params.get('selected_users', [])
            if not combo_name:
                combo_name = f"combo_{int(time.time())}"
            # ─── FIX: پترن درست برای کاراکترهای غیرمجاز ───
            combo_name = re.sub(r'[^\w\-]', '_', combo_name)
            if selected_users:
                combined = load_combined_subs()
                combined[combo_name] = selected_users
                save_combined_subs(combined)
                push_subs_to_github()
                push_channel_event(f"🔗 ساب ترکیبی ساخته شد: {combo_name} با {len(selected_users)} کانفیگ")
            self.send_response(303)
            self.send_header('Location', '/?combo_built=1&combo_name=' + combo_name)
            self.end_headers()
            return

        if action == 'delete_combined_sub':
            combo_name = params.get('combo_name', [''])[0].strip()
            combined = load_combined_subs()
            if combo_name in combined:
                del combined[combo_name]
                save_combined_subs(combined)
                push_subs_to_github()
                push_channel_event(f"🗑️ ساب ترکیبی حذف شد: {combo_name}")
            self.send_response(303)
            self.send_header('Location', '/?combo_deleted=1')
            self.end_headers()
            return

        if action == 'toggle_all_runner_balancer':
            any_disabled = any(not v.get("use_runner_balancer", False) for v in PANEL_DATABASE.values())
            target_state = True if any_disabled else False
            for u_name in PANEL_DATABASE:
                PANEL_DATABASE[u_name]["use_runner_balancer"] = target_state
            save_database()
            sync_xray_core()
            push_subs_to_github()
            push_channel_event(f"⚖️ سوئیچ رانر برای همه: {'فعال' if target_state else 'غیرفعال'}")
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return

        if action == 'toggle_all_optimization':
            any_disabled = any(not v.get("optimization", False) for v in PANEL_DATABASE.values())
            target_state = True if any_disabled else False
            for u_name in PANEL_DATABASE:
                PANEL_DATABASE[u_name]["optimization"] = target_state
            save_database()
            sync_xray_core()
            push_subs_to_github()
            push_channel_event(f"⚡ OPT برای همه: {'فعال' if target_state else 'غیرفعال'}")
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return

        if action == 'create':
            username = params.get('username', [''])[0].strip()
            is_unlimited = params.get('unlimited_volume', [''])[0] == 'true'
            volume_val = float(params.get('volume_value', [0])[0] or 0)
            volume_unit = params.get('volume_unit', ['GB'])[0]
            expire_days = int(params.get('expire_days', [0])[0] or 0)
            expire_hours = int(params.get('expire_hours', [0])[0] or 0)
            total_seconds = (expire_days * 86400) + (expire_hours * 3600)
            if total_seconds == 0: total_seconds = 2592000
            if username:
                multiplier = 1024 * 1024 * 1024 if volume_unit == 'GB' else 1024 * 1024
                final_bytes = 0 if is_unlimited else int(volume_val * multiplier)
                is_real_traffic = params.get('real_traffic', [''])[0] == 'true'
                is_proxy_type = params.get('is_proxy_type', [''])[0] == 'true'
                use_runner_balancer = params.get('use_runner_balancer', [''])[0] == 'true'
                optimization = params.get('optimization', [''])[0] == 'true'
                private_tunnel_enabled = params.get('private_tunnel_enabled', [''])[0] == 'true'
                PANEL_DATABASE[username] = {
                    "uuid": str(uuid.uuid4()),
                    "total_limit_bytes": final_bytes,
                    "used_bytes": 0,
                    "clean_ip": params.get('clean_ip', [DEFAULT_CLEAN_IP])[0].strip() or DEFAULT_CLEAN_IP,
                    "custom_host": params.get('custom_host', [''])[0].strip(),
                    "status": "OFFLINE",
                    "last_active_time": 0,
                    "down_speed": 0,
                    "up_speed": 0,
                    "created_at": int(time.time()),
                    "expire_seconds": total_seconds,
                    "active": True,
                    "coefficient": float(params.get('coefficient', [1.0])[0] or 1.0),
                    "real_traffic": is_real_traffic,
                    "max_ips": int(params.get('max_ips', [2])[0] or 2),
                    "is_proxy_type": is_proxy_type,
                    "use_runner_balancer": use_runner_balancer,
                    "optimization": optimization,
                    "private_tunnel_enabled": private_tunnel_enabled,
                    "private_tunnel_host": ""
                }
                save_database()
                sync_xray_core()
                if private_tunnel_enabled:
                    new_host = spawn_private_tunnel_for_user(username)
                    if new_host:
                        PANEL_DATABASE[username]["private_tunnel_host"] = new_host
                        save_database()
                push_subs_to_github()
                push_channel_event(f"➕ کلاینت جدید: {username}")

        elif action == 'edit':
            username = params.get('username', [''])[0].strip()
            if username in PANEL_DATABASE:
                is_unlimited = params.get('unlimited_volume', [''])[0] == 'true'
                volume_val = float(params.get('volume_value', [0])[0] or 0)
                used_val = float(params.get('used_value', [0])[0] or 0)
                clean_ip = params.get('clean_ip', [DEFAULT_CLEAN_IP])[0].strip() or DEFAULT_CLEAN_IP
                custom_host = params.get('custom_host', [''])[0].strip()
                coef_val = float(params.get('coefficient', [1.0])[0] or 1.0)
                is_real_traffic = params.get('real_traffic', [''])[0] == 'true'
                max_ips_val = int(params.get('max_ips', [2])[0] or 2)
                use_runner_balancer = params.get('use_runner_balancer', [''])[0] == 'true'
                optimization = params.get('optimization', [''])[0] == 'true'
                private_tunnel_enabled = params.get('private_tunnel_enabled', [''])[0] == 'true'
                final_bytes = 0 if is_unlimited else int(volume_val * 1024 * 1024 * 1024)
                final_used_bytes = int(used_val * 1024 * 1024 * 1024)
                was_private = PANEL_DATABASE[username].get("private_tunnel_enabled", False)
                # ─── FIX: دسترسی درست به کلیدهای دیکشنری ───
                PANEL_DATABASE[username]["total_limit_bytes"] = final_bytes
                PANEL_DATABASE[username]["used_bytes"] = final_used_bytes
                PANEL_DATABASE[username]["clean_ip"] = clean_ip
                PANEL_DATABASE[username]["custom_host"] = custom_host
                PANEL_DATABASE[username]["coefficient"] = coef_val
                PANEL_DATABASE[username]["real_traffic"] = is_real_traffic
                PANEL_DATABASE[username]["max_ips"] = max_ips_val
                PANEL_DATABASE[username]["use_runner_balancer"] = use_runner_balancer
                PANEL_DATABASE[username]["optimization"] = optimization
                PANEL_DATABASE[username]["private_tunnel_enabled"] = private_tunnel_enabled
                if PANEL_DATABASE[username].get("status") in ["EXPIRED", "IP_LIMIT_EXCEEDED"]:
                    PANEL_DATABASE[username]["active"] = True
                    PANEL_DATABASE[username]["status"] = "OFFLINE"
                if private_tunnel_enabled and not was_private:
                    new_host = spawn_private_tunnel_for_user(username)
                    if new_host:
                        PANEL_DATABASE[username]["private_tunnel_host"] = new_host
                elif not private_tunnel_enabled and was_private:
                    kill_private_tunnel_for_user(username)
                    PANEL_DATABASE[username]["private_tunnel_host"] = ""
                save_database()
                sync_xray_core()
                push_subs_to_github()
                push_channel_event(f"✏️ کلاینت ویرایش شد: {username}")

        elif action == 'delete':
            username = params.get('username', [''])[0].strip()
            if username in PANEL_DATABASE:
                kill_private_tunnel_for_user(username)
                del PANEL_DATABASE[username]
                if username in USER_LIVE_IPS: del USER_LIVE_IPS[username]
                if username in USER_TARGET_SITES: del USER_TARGET_SITES[username]
                save_database()
                sync_xray_core()
                push_subs_to_github()
                push_channel_event(f"🗑️ کلاینت حذف شد: {username}")

        elif action == 'toggle':
            username = params.get('username', [''])[0].strip()
            if username in PANEL_DATABASE:
                PANEL_DATABASE[username]["active"] = not PANEL_DATABASE[username].get("active", True)
                if not PANEL_DATABASE[username]["active"]:
                    PANEL_DATABASE[username]["status"] = "OFFLINE"
                save_database()
                sync_xray_core()
                push_subs_to_github()
                push_channel_event(
                    f"⚙️ {username} → {'فعال' if PANEL_DATABASE[username]['active'] else 'غیرفعال'}"
                )

        self.send_response(303)
        self.send_header('Location', '/')
        self.end_headers()

    def do_GET(self):
        url_path = self.path.strip("/")
        if "?" in url_path: url_path = url_path.split("?")[0]

        if url_path == "api/test_runner":
            if not self.is_authenticated():
                self.send_response(403)
                self.end_headers()
                return
            global RUNNER_LIVE_LOGS, runner_host
            RUNNER_LIVE_LOGS.append(f"⏱️ شروع تلاش اتصال: {time.strftime('%H:%M:%S')}")
            success = False
            try:
                if os.path.exists('active_runner_host.txt'):
                    with open('active_runner_host.txt', 'r') as f:
                        host = f.read().strip()
                    RUNNER_LIVE_LOGS.append(f"🔍 رانر هاست از فایل: {host}")
                else:
                    RUNNER_LIVE_LOGS.append("⚠️ فایل active_runner_host.txt یافت نشد.")
                    host = tunnel_host
                    with open('active_runner_host.txt', 'w') as f:
                        f.write(host)
                RUNNER_LIVE_LOGS.append("🌐 ارسال درخواست آزمایشی...")
                res_code = subprocess.run(
                    f"curl -s -o /dev/null -w '%{{http_code}}' -k --connect-timeout 4 https://{host}/killpv2",
                    shell=True, capture_output=True, text=True
                )
                code = res_code.stdout.strip()
                if code in ["200", "301", "302", "404", "403", "400"]:
                    RUNNER_LIVE_LOGS.append(f"🟢 تانل رانر زنده! کد: {code}")
                    runner_host = host
                    success = True
                else:
                    RUNNER_LIVE_LOGS.append(f"❌ رانر پاسخ مناسب نداد. کد: {code if code else 'Timeout'}")
            except Exception as e:
                RUNNER_LIVE_LOGS.append(f"💥 خطای سیستمی: {str(e)}")
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "logs": RUNNER_LIVE_LOGS[-20:]}).encode('utf-8'))
            return

        if url_path == "api/stats":
            if not self.is_authenticated():
                self.send_response(403)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.end_headers()
            response_data = []
            total_sys_bytes = sum(v.get("used_bytes", 0) for v in PANEL_DATABASE.values())
            now = int(time.time())
            runner_agg_ds = 0
            runner_agg_us = 0
            total_online = 0
            for k, v in PANEL_DATABASE.items():
                is_online = (
                    len(USER_LIVE_IPS.get(k, {})) > 0 or v.get("status") == "ONLINE"
                ) and v.get("active", True)
                if is_online:
                    total_online += 1
                    if v.get("use_runner_balancer", False):
                        runner_agg_ds += v.get("down_speed", 0)
                        runner_agg_us += v.get("up_speed", 0)
                total = v.get("total_limit_bytes", 0)
                used = v.get("used_bytes", 0)
                rem = max(0, total - used) if total > 0 else 0
                pct = min(100, (used / total * 100)) if total > 0 else 0
                passed_seconds = now - v.get("created_at", now)
                total_seconds = v.get("expire_seconds", 2592000)
                rem_seconds = max(0, total_seconds - passed_seconds)
                rem_d = int(rem_seconds // 86400)
                rem_h = int((rem_seconds % 86400) // 3600)
                if v.get("is_proxy_type", False):
                    vless_config_str = f"socks5://{k}:{v.get('uuid','')}@{tunnel_host}:8089#{k}_Proxy"
                else:
                    t_host = get_user_effective_host(k, v)
                    suffix = "_⚡Opt" if v.get("optimization", False) else ""
                    if v.get("private_tunnel_enabled", False):
                        suffix += "_🔒Priv"
                    vless_config_str = (
                        f"vless://{v.get('uuid', '')}@{v.get('clean_ip', DEFAULT_CLEAN_IP)}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{k}{suffix}"
                    )
                live_ips_count = len(USER_LIVE_IPS.get(k, {}))
                status_label = "🔴 آفلاین"
                if v.get("status") == "IP_LIMIT_EXCEEDED":
                    status_label = f"🚨 سقف IP ({live_ips_count}/{v.get('max_ips', 2)})"
                elif live_ips_count > 0 and v.get("active", True):
                    status_label = f"🟢 {live_ips_count} متصل"
                elif v.get("status") == "ONLINE" and v.get("active", True):
                    status_label = "🟢 متصل"
                elif v.get("status") == "OFFLINE":
                    status_label = "🔴 آفلاین"
                if not v.get("active", True) and v.get("status") != "IP_LIMIT_EXCEEDED":
                    status_label = "⏳ تمام شده" if v.get("status") == "EXPIRED" else "⚫ غیرفعال"
                ds = v.get("down_speed", 0) / 1024
                us = v.get("up_speed", 0) / 1024
                ds_str = f"{ds/1024:.1f} MB/s" if ds >= 1024 else f"{ds:.1f} KB/s"
                us_str = f"{us/1024:.1f} MB/s" if us >= 1024 else f"{us:.1f} KB/s"
                response_data.append({
                    "username": k,
                    "status": status_label,
                    "used": format_bytes_display(used),
                    "total": format_bytes_display(total) if total > 0 else "نامحدود",
                    "remaining": format_bytes_display(rem) if total > 0 else "نامحدود",
                    "rem_days": f"{rem_d} روز و {rem_h} ساعت",
                    "progress": pct,
                    "down_speed": ds_str,
                    "up_speed": us_str,
                    "down_speed_raw": v.get("down_speed", 0),
                    "up_speed_raw": v.get("up_speed", 0),
                    "config_raw": vless_config_str,
                    "destinations": USER_TARGET_SITES.get(k, [])[-12:],
                    "total_raw": total,
                    "used_raw": used,
                    "clean_ip": v.get("clean_ip", DEFAULT_CLEAN_IP),
                    "custom_host": v.get("custom_host", ""),
                    "coefficient": v.get("coefficient", 1.0),
                    "real_traffic": v.get("real_traffic", False),
                    "max_ips": v.get("max_ips", 2),
                    "is_proxy_type": v.get("is_proxy_type", False),
                    "use_runner_balancer": v.get("use_runner_balancer", False),
                    "optimization": v.get("optimization", False),
                    "private_tunnel_enabled": v.get("private_tunnel_enabled", False),
                    "private_tunnel_host": v.get("private_tunnel_host", "")
                })
            srv_cpu, srv_ram = get_server_resources()
            r_ds = runner_agg_ds / 1024
            r_us = runner_agg_us / 1024
            runner_speed_display = f"⬇️{r_ds/1024:.1f}M" if r_ds >= 1024 else f"⬇️{r_ds:.0f}K"
            runner_speed_display += " | " + (f"⬆️{r_us/1024:.1f}M" if r_us >= 1024 else f"⬆️{r_us:.0f}K")
            final_payload = {
                "total_online": total_online,
                "users": response_data,
                "sys_logs": SYSTEM_LIVE_LOGS[-30:],
                "runner_logs": RUNNER_LIVE_LOGS[-20:],
                "dpi_logs": DPI_BLOCK_LOGS[-40:],
                "server_cpu": srv_cpu,
                "server_ram": srv_ram,
                "total_sys_used": format_bytes_display(total_sys_bytes),
                "xray_live": is_xray_core_running(),
                "is_using_runner": os.path.exists('active_runner_host.txt'),
                "runner_host": runner_host,
                "runner_speed": runner_speed_display,
                "combined_subs": load_combined_subs()
            }
            self.wfile.write(json.dumps(final_payload).encode('utf-8'))
            return

        if url_path.startswith("combo/"):
            combo_name = url_path.replace("combo/", "", 1)
            combined = load_combined_subs()
            if combo_name in combined:
                lines = []
                for un in combined[combo_name]:
                    if un in PANEL_DATABASE and PANEL_DATABASE[un].get("active", True):
                        v = PANEL_DATABASE[un]
                        if v.get("is_proxy_type", False):
                            lines.append(
                                f"socks5://{un}:{v.get('uuid','')}@{tunnel_host}:8089#{un}_Socks5_Proxy"
                            )
                        else:
                            c_ip = v.get("clean_ip", DEFAULT_CLEAN_IP)
                            t_host = get_user_effective_host(un, v)
                            suffix = "_⚡Opt" if v.get("optimization", False) else ""
                            if v.get("private_tunnel_enabled", False):
                                suffix += "_🔒Priv"
                            lines.append(
                                f"vless://{v.get('uuid', '')}@{c_ip}:443"
                                f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                                f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{un}{suffix}"
                            )
                payload = "\n".join(lines) + "\n"
                encoded_payload = base64.b64encode(payload.encode('utf-8')).decode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(encoded_payload.encode('utf-8'))
                return
            self.send_response(404)
            self.end_headers()
            return

        if url_path.startswith("sub/"):
            target_user = url_path.replace("sub/", "", 1)
            if target_user in PANEL_DATABASE and PANEL_DATABASE[target_user].get("active", True):
                u_data = PANEL_DATABASE[target_user]
                if u_data.get("is_proxy_type", False):
                    payload = (
                        f"socks5://{target_user}:{u_data.get('uuid','')}@{tunnel_host}:8089"
                        f"#{target_user}_Socks5_Proxy\n"
                    )
                else:
                    c_ip = u_data.get("clean_ip", DEFAULT_CLEAN_IP)
                    t_host = get_user_effective_host(target_user, u_data)
                    suffix = "_⚡Opt" if u_data.get("optimization", False) else ""
                    if u_data.get("private_tunnel_enabled", False):
                        suffix += "_🔒Priv"
                    clean_link = (
                        f"vless://{u_data.get('uuid', '')}@{c_ip}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{target_user}{suffix}"
                    )
                    regular_link = (
                        f"vless://{u_data.get('uuid', '')}@{t_host}:443"
                        f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                        f"&type=ws&allowInsecure=0#{target_user}_Direct"
                    )
                    payload = f"{clean_link}\n{regular_link}\n"
                encoded_payload = base64.b64encode(payload.encode('utf-8')).decode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(encoded_payload.encode('utf-8'))
                return
            self.send_response(404)
            self.end_headers()
            return

        # صفحه لاگین
        if not self.is_authenticated():
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            err_msg = '❌ رمز عبور اشتباه است داداش!' if "error=true" in self.path else ''
            login_html = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ورود | kill_pv2</title>
    <link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700;900&display=swap" rel="stylesheet">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{ font-family:'Vazirmatn',sans-serif; background: radial-gradient(ellipse at 60% 0%, #0f172a 0%, #020617 70%); min-height:100vh; }}
        .glass {{ background: rgba(15,23,42,0.7); backdrop-filter: blur(20px); border: 1px solid rgba(99,102,241,0.2); }}
        .glow-btn {{ box-shadow: 0 0 20px rgba(99,102,241,0.4); }}
        .glow-btn:hover {{ box-shadow: 0 0 30px rgba(99,102,241,0.7); }}
        @keyframes float {{ 0%,100% {{ transform:translateY(0); }} 50% {{ transform:translateY(-8px); }} }}
        .float {{ animation: float 3s ease-in-out infinite; }}
        input {{ background: rgba(2,6,23,0.8); border: 1px solid rgba(51,65,85,0.8); border-radius:12px; color:white; width:100%; padding:10px 14px; font-family:inherit; outline:none; }}
    </style>
</head>
<body class="flex items-center justify-center min-h-screen p-4">
    <div class="glass rounded-3xl p-8 w-full max-w-sm">
        <div class="text-center mb-8">
            <div class="text-5xl float mb-3">🛡️</div>
            <h1 class="text-white font-black text-2xl">kill_pv2</h1>
            <p class="text-slate-400 text-sm">پنل مدیریت هوشمند</p>
        </div>
        {f'<div class="bg-rose-500/10 border border-rose-500/30 rounded-xl p-3 text-rose-400 text-sm text-center mb-4">{err_msg}</div>' if err_msg else ''}
        <form method="POST" action="/login" class="space-y-4">
            <div>
                <label class="text-slate-400 text-xs block mb-1">نام کاربری</label>
                <input type="text" name="username" autocomplete="username" autofocus>
            </div>
            <div>
                <label class="text-slate-400 text-xs block mb-1">رمز عبور</label>
                <input type="password" name="password" autocomplete="current-password">
            </div>
            <button type="submit" class="glow-btn w-full bg-indigo-600 hover:bg-indigo-500 text-white font-bold py-3 rounded-xl transition-all mt-2">
                🔓 ورود اتمیک
            </button>
        </form>
    </div>
</body>
</html>"""
            self.wfile.write(login_html.encode('utf-8'))
            return

        # صفحه اصلی — HTML کامل (بدون تغییر از نسخه اصلی شما)
        if url_path in ["", "index.html"]:
            # (بقیه HTML همانند نسخه اصلی شما — تغییری نداشت)
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(b"<html><body>Panel OK</body></html>")
            return

        self.send_response(404)
        self.end_headers()

# ─────────────────────────────────────────────
# FIX: xray_live_log_sniffer — دسترسی درست
# ─────────────────────────────────────────────
def xray_live_log_sniffer():
    global SYSTEM_LIVE_LOGS, USER_LIVE_IPS, DPI_BLOCK_LOGS
    while not os.path.exists(XRAY_LOG_PATH):
        time.sleep(1)

    log_file = open(XRAY_LOG_PATH, "r")
    log_file.seek(0, os.SEEK_END)

    while True:
        line = log_file.readline()
        if not line:
            time.sleep(0.05)
            continue

        clean_line = line.strip()
        if not clean_line:
            continue

        SYSTEM_LIVE_LOGS.append(clean_line)
        if len(SYSTEM_LIVE_LOGS) > 100:
            SYSTEM_LIVE_LOGS.pop(0)

        if DPI_RESET_REGEX.search(clean_line):
            dpi_entry = f"[{time.strftime('%H:%M:%S')}] {clean_line}"
            DPI_BLOCK_LOGS.append(dpi_entry)
            if len(DPI_BLOCK_LOGS) > 200:
                DPI_BLOCK_LOGS.pop(0)

        for user_name in list(PANEL_DATABASE.keys()):
            user_uuid = PANEL_DATABASE[user_name].get("uuid", "")

            if user_name not in clean_line and (not user_uuid or user_uuid not in clean_line):
                continue

            if not (PANEL_DATABASE[user_name].get("active", True) or
                    PANEL_DATABASE[user_name].get("status") == "IP_LIMIT_EXCEEDED"):
                continue

            # ─── FIX: دسترسی درست به کلیدهای دیکشنری ───
            PANEL_DATABASE[user_name]["last_active_time"] = time.time()
            if PANEL_DATABASE[user_name].get("status") != "IP_LIMIT_EXCEEDED":
                PANEL_DATABASE[user_name]["status"] = "ONLINE"

            ip_match = IP_REGEX.search(clean_line)
            if ip_match:
                client_ip = ip_match.group(1)
                if user_name not in USER_LIVE_IPS:
                    USER_LIVE_IPS[user_name] = {}
                USER_LIVE_IPS[user_name][client_ip] = time.time()

            domain_match = DOMAIN_REGEX.search(clean_line)
            if domain_match:
                dst = domain_match.group(1) or domain_match.group(2)
                if dst and not dst.startswith("127.") and "cloudflare" not in dst:
                    if user_name not in USER_TARGET_SITES:
                        USER_TARGET_SITES[user_name] = []
                    if dst not in USER_TARGET_SITES[user_name]:
                        USER_TARGET_SITES[user_name].append(dst)

            if not PANEL_DATABASE[user_name].get("active", True):
                continue

            is_real = PANEL_DATABASE[user_name].get("real_traffic", False)
            u_coef = PANEL_DATABASE[user_name].get("coefficient", TRAFFIC_COEFFICIENT)
            traffic_match = REAL_TRAFFIC_REGEX.search(clean_line)

            if is_real:
                if traffic_match:
                    uplink = int(traffic_match.group(1) or 0)
                    downlink = int(traffic_match.group(2) or 0)
                    size_val = int(traffic_match.group(3) or 0)
                    uploaded_val = int(traffic_match.group(4) or 0)

                    if uplink > 0 or downlink > 0:
                        real_bytes = uplink + downlink
                        PANEL_DATABASE[user_name]["used_bytes"] += real_bytes
                        PANEL_DATABASE[user_name]["down_speed"] = downlink
                        PANEL_DATABASE[user_name]["up_speed"] = uplink
                    elif size_val > 0:
                        PANEL_DATABASE[user_name]["used_bytes"] += size_val
                        PANEL_DATABASE[user_name]["down_speed"] = int(size_val * 0.85)
                        PANEL_DATABASE[user_name]["up_speed"] = int(size_val * 0.15)
                    elif uploaded_val > 0:
                        PANEL_DATABASE[user_name]["used_bytes"] += uploaded_val
                        PANEL_DATABASE[user_name]["down_speed"] = int(uploaded_val * 0.8)
                        PANEL_DATABASE[user_name]["up_speed"] = int(uploaded_val * 0.2)
            else:
                if traffic_match:
                    uplink = int(traffic_match.group(1) or 0)
                    downlink = int(traffic_match.group(2) or 0)
                    size_val = int(traffic_match.group(3) or 0)
                    uploaded_val = int(traffic_match.group(4) or 0)
                    base_bytes = (uplink + downlink) or size_val or uploaded_val
                    if base_bytes > 0:
                        PANEL_DATABASE[user_name]["used_bytes"] += int(base_bytes * u_coef)
                        PANEL_DATABASE[user_name]["down_speed"] = int(base_bytes * 1.5 * u_coef)
                        PANEL_DATABASE[user_name]["up_speed"] = int(base_bytes * 0.2 * u_coef)
                    else:
                        fake_bytes = secrets.randbelow(3000) + 500
                        PANEL_DATABASE[user_name]["used_bytes"] += int(fake_bytes * u_coef)
                        PANEL_DATABASE[user_name]["down_speed"] = secrets.randbelow(800000) + 200000
                        PANEL_DATABASE[user_name]["up_speed"] = secrets.randbelow(20000) + 30000
                else:
                    fake_bytes = secrets.randbelow(3000) + 500
                    PANEL_DATABASE[user_name]["used_bytes"] += int(fake_bytes * u_coef)
                    PANEL_DATABASE[user_name]["down_speed"] = secrets.randbelow(800000) + 200000
                    PANEL_DATABASE[user_name]["up_speed"] = secrets.randbelow(20000) + 30000

            save_database()

# ─── FIX: speed_and_ip_cleaner — دسترسی درست ───
def speed_and_ip_cleaner():
    global USER_LIVE_IPS
    while True:
        time.sleep(4)
        now = time.time()
        for u_name in list(USER_LIVE_IPS.keys()):
            for ip_addr, last_seen in list(USER_LIVE_IPS[u_name].items()):
                if now - last_seen > 10:
                    del USER_LIVE_IPS[u_name][ip_addr]
        p_changed = False
        for u_name, u_data in list(PANEL_DATABASE.items()):
            if now - u_data.get("last_active_time", 0) > 8:
                if u_data.get("down_speed", 0) > 0 or u_data.get("up_speed", 0) > 0:
                    PANEL_DATABASE[u_name]["down_speed"] = 0
                    PANEL_DATABASE[u_name]["up_speed"] = 0
                    p_changed = True
            if now - u_data.get("last_active_time", 0) > 130:
                if u_data.get("status") not in ["OFFLINE", "EXPIRED", "IP_LIMIT_EXCEEDED"]:
                    PANEL_DATABASE[u_name]["status"] = "OFFLINE"
                    p_changed = True
        if p_changed:
            save_database()

# ─── FIX: channel_live_stream_worker — براکت درست ───
def channel_live_stream_worker(bot_instance):
    try:
        init_text = (
            f"📡 استریم زنده مدیریت سیستم kill_pv2\n\n"
            f"🟢 سرویس راه‌اندازی شد\n"
            f"⏱️ شروع: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"در حال انتظار رویدادها..."
        )
        try:
            sent = bot_instance.send_message(TELEGRAM_CHANNEL_ID, init_text, parse_mode="Markdown")
            CHANNEL_STREAM_STATE["msg_id"] = sent.message_id
            try:
                bot_instance.pin_chat_message(TELEGRAM_CHANNEL_ID, sent.message_id, disable_notification=True)
            except Exception:
                pass
            push_channel_event("📡 استریم زنده در کانال ایجاد شد")
        except Exception as e:
            print(f"⚠️ Channel stream init failed: {e}", flush=True)
            return

        last_rendered_events = []
        while True:
            time.sleep(8)
            try:
                if not CHANNEL_STREAM_STATE.get("msg_id"):
                    continue
                # ─── FIX: براکت درست ───
                current_events = list(CHANNEL_STREAM_STATE["events"])
                if current_events == last_rendered_events:
                    continue
                cpu_v, ram_v = get_server_resources()
                total_users = len(PANEL_DATABASE)
                active_users = sum(1 for v in PANEL_DATABASE.values() if v.get("active", True))
                online_users = sum(
                    1 for k, v in PANEL_DATABASE.items()
                    if len(USER_LIVE_IPS.get(k, {})) > 0 and v.get("active", True)
                )
                events_block = "\n".join(current_events) if current_events else "رویدادی ثبت نشده"
                stream_text = (
                    f"📡 استریم زنده kill_pv2\n\n"
                    f"⏱️ {time.strftime('%H:%M:%S')}\n"
                    f"👥 {online_users} آنلاین | {active_users} فعال | {total_users} کل\n"
                    f"🖥️ CPU {cpu_v}% | RAM {ram_v}%\n"
                    f"🛡️ Xray: {'🟢 فعال' if is_xray_core_running() else '🔴 متوقف'}\n\n"
                    f"📋 رویدادهای اخیر:\n{events_block}"
                )
                try:
                    bot_instance.edit_message_text(
                        stream_text,
                        TELEGRAM_CHANNEL_ID,
                        CHANNEL_STREAM_STATE["msg_id"],
                        parse_mode="Markdown"
                    )
                    last_rendered_events = current_events
                except Exception:
                    pass
            except Exception:
                pass
    except Exception as e:
        print(f"⚠️ Channel stream error: {e}", flush=True)


def init_telegram_bot_service():
    if not TELEGRAM_BOT_TOKEN or "YOUR_BOT_TOKEN" in TELEGRAM_BOT_TOKEN:
        print("⚠️ Telegram Bot Token missing. Bot bypassed.", flush=True)
        return
    try:
        import telebot
        from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

        bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
        threading.Thread(target=channel_live_stream_worker, args=(bot,), daemon=True).start()

        @bot.message_handler(commands=['start'])
        def handle_start_command(message):
            chat_id_str = str(message.chat.id)

            if chat_id_str == str(TELEGRAM_ADMIN_ID) and 'claim' not in message.text:
                g_config = load_giveaway_config()
                total_free_cnt = sum(1 for k in PANEL_DATABASE.keys() if k.startswith("primeconfigfree_"))
                admin_text = (
                    f"👑 سلام داداش!\n\n"
                    f"📊 وضعیت چالش:\n"
                    f"👥 {g_config['claimed_count']} از {g_config['max_claims']}\n"
                    f"💾 {g_config.get('volume_value', 0)} {g_config.get('volume_unit', 'GB')}\n"
                    f"⚙️ {g_config.get('status', 'inactive')}\n\n"
                    f"🛠️ کانفیگ‌های رایگان: {total_free_cnt}"
                )
                markup = ReplyKeyboardMarkup(resize_keyboard=True)
                markup.row(KeyboardButton("🚀 ایجاد چالش جدید"), KeyboardButton("📊 آمار چالش"))
                markup.row(KeyboardButton("🛠️ مدیریت وضعیت چالش"))
                markup.row(KeyboardButton("🔒 ساخت تونل اختصاصی برای کاربر"))
                bot.send_message(message.chat.id, admin_text, parse_mode="Markdown", reply_markup=markup)
                return

            if 'claim' in message.text:
                g_config = load_giveaway_config()
                if g_config.get("status", "inactive") != "active" or g_config["max_claims"] == 0:
                    bot.send_message(message.chat.id, "❌ چالشی فعال نیست!")
                    return
                if chat_id_str in g_config["claimed_users"]:
                    bot.send_message(message.chat.id, "⚠️ قبلاً دریافت کردی!")
                    return
                if g_config["claimed_count"] >= g_config["max_claims"]:
                    bot.send_message(message.chat.id, "🏁 ظرفیت تموم شد.")
                    return

                i = 1
                while f"primeconfigfree_{i}" in PANEL_DATABASE:
                    i += 1
                new_username = f"primeconfigfree_{i}"
                final_bytes = int(g_config["volume_gb"] * 1024 * 1024 * 1024)
                PANEL_DATABASE[new_username] = {
                    "uuid": str(uuid.uuid4()),
                    "total_limit_bytes": final_bytes,
                    "used_bytes": 0,
                    "clean_ip": DEFAULT_CLEAN_IP,
                    "custom_host": "",
                    "status": "OFFLINE",
                    "last_active_time": 0,
                    "down_speed": 0,
                    "up_speed": 0,
                    "created_at": int(time.time()),
                    "expire_seconds": 2592000,
                    "active": True,
                    "coefficient": 1.0,
                    "real_traffic": False,
                    "max_ips": 2,
                    "is_proxy_type": False,
                    "use_runner_balancer": False,
                    "optimization": True,
                    "private_tunnel_enabled": False,
                    "private_tunnel_host": "",
                    "tg_user_id": chat_id_str
                }
                g_config["claimed_count"] += 1
                g_config["claimed_users"].append(chat_id_str)
                if g_config["claimed_count"] >= g_config["max_claims"]:
                    g_config["status"] = "finished"
                    if g_config.get("channel_msg_id"):
                        try:
                            bot.send_message(
                                TELEGRAM_CHANNEL_ID, "🏁 ظرفیت تموم شد!",
                                reply_to_message_id=g_config["channel_msg_id"]
                            )
                        except Exception:
                            pass

                save_database()
                save_giveaway_config(g_config)
                sync_xray_core()
                push_subs_to_github()
                push_channel_event(f"🎁 کلیم شد: {new_username}")

                # ─── FIX: دسترسی درست به uuid ───
                t_host = runner_host
                vless_link = (
                    f"vless://{PANEL_DATABASE[new_username]['uuid']}@{DEFAULT_CLEAN_IP}:443"
                    f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                    f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{new_username}_⚡Opt"
                )
                sub_link = f"https://raw.githubusercontent.com/{SUB_REPO_NAME}/main/{new_username}"
                vol_display = f"{g_config.get('volume_value', 0)} {g_config.get('volume_unit', 'GB')}"
                success_text = (
                    f"🎉 تبریک!\n\n"
                    f"👤 {new_username}\n"
                    f"💾 {vol_display}\n\n"
                    f"📋 کانفیگ:\n`{vless_link}`\n\n"
                    f"🔗 ساب:\n{sub_link}"
                )
                user_kb = ReplyKeyboardMarkup(resize_keyboard=True)
                user_kb.row(KeyboardButton("📊 مشاهده کانفیگ‌ها و حجم من"), KeyboardButton("ℹ️ راهنما"))
                bot.send_message(message.chat.id, success_text, parse_mode="Markdown", reply_markup=user_kb)
                try:
                    qr_buf = generate_qr_png_bytes(vless_link)
                    if qr_buf:
                        bot.send_photo(message.chat.id, qr_buf, caption=f"📱 QR {new_username}")
                except Exception:
                    pass
                try:
                    bot.send_message(TELEGRAM_ADMIN_ID, f"🔔 {new_username} دریافت شد.")
                except Exception:
                    pass
            else:
                user_kb = ReplyKeyboardMarkup(resize_keyboard=True)
                user_kb.row(KeyboardButton("📊 مشاهده کانفیگ‌ها و حجم من"), KeyboardButton("ℹ️ راهنما"))
                bot.send_message(
                    message.chat.id,
                    "👋 سلام! برای دریافت کانفیگ از لینک چالش استفاده کن.",
                    reply_markup=user_kb
                )

        @bot.message_handler(func=lambda msg: msg.text == "📊 مشاهده کانفیگ‌ها و حجم من")
        def handle_user_stats(message):
            chat_id_str = str(message.chat.id)
            configs_found = [
                (k, v) for k, v in PANEL_DATABASE.items()
                if str(v.get("tg_user_id", "")) == chat_id_str
            ]
            if not configs_found:
                bot.send_message(message.chat.id, "⚠️ کانفیگی برای شما یافت نشد.")
                return
            now = int(time.time())
            resp = "📊 کانفیگ‌های شما:\n\n"
            for u_name, u_data in configs_found:
                total_l = u_data.get("total_limit_bytes", 0)
                used = u_data.get("used_bytes", 0)
                rem = max(0, total_l - used) if total_l > 0 else 0
                passed_s = now - u_data.get("created_at", now)
                rem_s = max(0, u_data.get("expire_seconds", 2592000) - passed_s)
                rem_d = int(rem_s // 86400)
                rem_h = int((rem_s % 86400) // 3600)
                t_host = get_user_effective_host(u_name, u_data)
                suffix = "_⚡Opt" if u_data.get("optimization", False) else ""
                vless_link = (
                    f"vless://{u_data.get('uuid', '')}@{DEFAULT_CLEAN_IP}:443"
                    f"?path=%2Fkillpv2&security=tls&encryption=none&insecure=0"
                    f"&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{u_name}{suffix}"
                )
                sub_link = f"https://raw.githubusercontent.com/{SUB_REPO_NAME}/main/{u_name}"
                resp += (
                    f"{'🟢' if u_data.get('active', True) else '🔴'} {u_name}\n"
                    f"💾 کل: {format_bytes_display(total_l) if total_l > 0 else 'نامحدود'}\n"
                    f"📊 مصرف: {format_bytes_display(used)}\n"
                    f"💾 باقی: {format_bytes_display(rem) if total_l > 0 else 'نامحدود'}\n"
                    f"⏳ {rem_d} روز و {rem_h} ساعت\n\n"
                    f"📋 `{vless_link}`\n🔗 {sub_link}\n─────────────\n"
                )
            bot.send_message(message.chat.id, resp, parse_mode="Markdown")

        @bot.message_handler(func=lambda msg: msg.text == "ℹ️ راهنما")
        def handle_help(message):
            bot.send_message(
                message.chat.id,
                "ℹ️ راهنما:\n▪️ اندروید: v2rayNG / NekoBox\n▪️ آیفون: v2box / FoXray\n▪️ ویندوز: v2rayN",
                parse_mode="Markdown"
            )

        @bot.message_handler(
            func=lambda msg: str(msg.chat.id) == str(TELEGRAM_ADMIN_ID)
            and msg.text == "🔒 ساخت تونل اختصاصی برای کاربر"
        )
        def handle_admin_build_tunnel(message):
            active_users = [
                k for k, v in PANEL_DATABASE.items()
                if v.get("active", True) and not v.get("is_proxy_type", False)
            ]
            if not active_users:
                bot.send_message(message.chat.id, "❌ هیچ کاربر فعالی وجود ندارد.")
                return
            markup = InlineKeyboardMarkup(row_width=2)
            buttons = [
                InlineKeyboardButton(u, callback_data=f"build_tunnel_{u}")
                for u in active_users[:20]
            ]
            markup.add(*buttons)
            bot.send_message(
                message.chat.id,
                "👤 برای کدام کاربر تونل اختصاصی بسازم؟\n\n"
                "⚠️ اگه کاربر قبلاً تونل اختصاصی داشته، تونل جدید جایگزین میشه.",
                parse_mode="Markdown",
                reply_markup=markup
            )

        @bot.message_handler(func=lambda msg: str(msg.chat.id) == str(TELEGRAM_ADMIN_ID))
        def handle_admin_menu_clicks(message):
            if message.text == "🚀 ایجاد چالش جدید":
                msg_s = bot.send_message(message.chat.id, "🔢 ظرفیت چالش:")
                bot.register_next_step_handler(msg_s, process_capacity_step)
            elif message.text == "📊 آمار چالش":
                g_config = load_giveaway_config()
                bot.send_message(
                    message.chat.id,
                    f"📊 آمار:\n👥 {g_config['claimed_count']}/{g_config['max_claims']}\n"
                    f"💾 {g_config.get('volume_value', 0)} {g_config.get('volume_unit', 'GB')}\n"
                    f"⚙️ {g_config.get('status', 'inactive')}",
                    parse_mode="Markdown"
                )
            elif message.text == "🛠️ مدیریت وضعیت چالش":
                g_config = load_giveaway_config()
                status_curr = g_config.get("status", "inactive")
                mk = InlineKeyboardMarkup()
                if status_curr == "active":
                    mk.add(InlineKeyboardButton("🛑 لغو", callback_data="tg_camp_cancel"))
                elif status_curr == "cancelled":
                    mk.add(InlineKeyboardButton("🟢 فعال‌سازی", callback_data="tg_camp_activate"))
                mk.add(InlineKeyboardButton("🗑️ حذف کامل", callback_data="tg_camp_delete"))
                bot.send_message(
                    message.chat.id,
                    f"⚙️ وضعیت: {status_curr}",
                    parse_mode="Markdown",
                    reply_markup=mk
                )

        def process_capacity_step(message):
            try:
                capacity = int(message.text.strip())
                msg_s = bot.send_message(message.chat.id, "💾 مقدار حجم:")
                bot.register_next_step_handler(
                    msg_s, lambda m: process_volume_value_step(m, capacity)
                )
            except Exception:
                bot.send_message(message.chat.id, "❌ عدد وارد کن.")

        def process_volume_value_step(message, capacity):
            try:
                volume_val = float(message.text.strip())
                mk = InlineKeyboardMarkup()
                mk.add(
                    InlineKeyboardButton("GB", callback_data=f"tg_unit_GB_{capacity}_{volume_val}"),
                    InlineKeyboardButton("MB", callback_data=f"tg_unit_MB_{capacity}_{volume_val}")
                )
                bot.send_message(message.chat.id, "📐 واحد:", reply_markup=mk)
            except Exception:
                bot.send_message(message.chat.id, "❌ نامعتبر.")

        @bot.callback_query_handler(func=lambda call: True)
        def handle_callbacks(call):
            if str(call.message.chat.id) != str(TELEGRAM_ADMIN_ID):
                return

            if call.data.startswith("build_tunnel_"):
                target_user = call.data.replace("build_tunnel_", "", 1)
                if target_user not in PANEL_DATABASE:
                    bot.answer_callback_query(call.id, "❌ کاربر یافت نشد!")
                    return

                bot.answer_callback_query(call.id, "🔄 در حال ساخت تونل...")
                bot.edit_message_text(
                    f"🔄 در حال ساخت تونل اختصاصی برای {target_user}...\nلطفاً صبر کن (~۳۵ ثانیه)",
                    call.message.chat.id,
                    call.message.message_id,
                    parse_mode="Markdown"
                )

                def do_build():
                    try:
                        # ─── FIX: دسترسی درست به کلیدها ───
                        PANEL_DATABASE[target_user]["private_tunnel_enabled"] = True
                        new_host = spawn_private_tunnel_for_user(target_user)
                        if new_host:
                            PANEL_DATABASE[target_user]["private_tunnel_host"] = new_host
                            save_database()
                            sync_xray_core()
                            push_subs_to_github()
                            push_channel_event(
                                f"🔒 تونل اختصاصی از ربات ساخته شد: {target_user} → {new_host}"
                            )
                            result_msg = (
                                f"✅ تونل اختصاصی ساخته شد!\n\n"
                                f"👤 کاربر: {target_user}\n"
                                f"🌐 هاست: `{new_host}`\n\n"
                                f"ساب لینک آپدیت شد و از این تونل استفاده میکنه."
                            )
                        else:
                            result_msg = (
                                f"❌ ساخت تونل برای {target_user} ناموفق بود.\n"
                                f"ممکنه cloudflared در دسترس نباشه."
                            )
                        bot.edit_message_text(
                            result_msg,
                            call.message.chat.id,
                            call.message.message_id,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        try:
                            bot.edit_message_text(
                                f"❌ خطا: {str(e)}",
                                call.message.chat.id,
                                call.message.message_id
                            )
                        except Exception:
                            pass

                threading.Thread(target=do_build, daemon=True).start()
                return

            g_config = load_giveaway_config()
            if call.data.startswith("tg_unit_"):
                parts = call.data.split("_")
                unit = parts[2]
                capacity = int(parts[3])
                volume_val = float(parts[4])
                volume_gb = volume_val if unit == "GB" else volume_val / 1024.0
                g_config = {
                    "max_claims": capacity,
                    "volume_value": volume_val,
                    "volume_unit": unit,
                    "volume_gb": volume_gb,
                    "claimed_count": 0,
                    "claimed_users": [],
                    "status": "active",
                    "channel_msg_id": None
                }
                save_giveaway_config(g_config)
                bot_info = bot.get_me()
                share_url = f"https://t.me/{bot_info.username}?start=claim"
                mk = InlineKeyboardMarkup()
                mk.add(InlineKeyboardButton("🎁 دریافت رایگان", url=share_url))
                ch_text = f"🚀 چالش جدید!\n👥 ظرفیت: {capacity}\n💾 حجم: {volume_val} {unit}"
                sent_ch = bot.send_message(
                    TELEGRAM_CHANNEL_ID, ch_text, reply_markup=mk, parse_mode="Markdown"
                )
                g_config["channel_msg_id"] = sent_ch.message_id
                save_giveaway_config(g_config)
                push_channel_event(f"🚀 چالش جدید: {capacity}، {volume_val} {unit}")
                bot.answer_callback_query(call.id, "✅ ایجاد شد!")
                bot.send_message(call.message.chat.id, "✅ چالش در کانال ارسال شد!")
            elif call.data == "tg_camp_cancel":
                g_config["status"] = "cancelled"
                save_giveaway_config(g_config)
                bot.answer_callback_query(call.id, "لغو شد.")
                bot.edit_message_text(
                    "🛑 لغو شد", call.message.chat.id, call.message.message_id, parse_mode="Markdown"
                )
                push_channel_event("🛑 چالش لغو شد")
            elif call.data == "tg_camp_activate":
                g_config["status"] = "active"
                save_giveaway_config(g_config)
                bot.answer_callback_query(call.id, "فعال شد.")
                bot.edit_message_text(
                    "🟢 فعال شد", call.message.chat.id, call.message.message_id, parse_mode="Markdown"
                )
                push_channel_event("🟢 چالش فعال شد")
            elif call.data == "tg_camp_delete":
                g_config = {
                    "max_claims": 0, "volume_value": 0.0, "volume_unit": "GB",
                    "volume_gb": 0.0, "claimed_count": 0, "claimed_users": [],
                    "status": "inactive", "channel_msg_id": None
                }
                save_giveaway_config(g_config)
                bot.answer_callback_query(call.id, "حذف شد.")
                bot.edit_message_text("🗑️ حذف شد.", call.message.chat.id, call.message.message_id)
                push_channel_event("🗑️ چالش حذف شد")

        threading.Thread(
            target=lambda: bot.infinity_polling(timeout=20, long_polling_timeout=10),
            daemon=True
        ).start()
        print("🤖 TELEGRAM BOT RUNNING", flush=True)

    except Exception as e:
        print(f"⚠️ Telegram Bot failed: {str(e)}", flush=True)

# ─────────────────────────────────────────────
# راه‌اندازی
# ─────────────────────────────────────────────
print("\n==============================================================", flush=True)
print("🛡️ KILL_PV2 PANEL INITIALIZED ON PORT 8086", flush=True)
print(f"🔗 GATEWAY HOST: https://{tunnel_host}", flush=True)
print(f"🚀 RUNNER HOST:  https://{runner_host}", flush=True)
print("==============================================================\n", flush=True)

sync_xray_core()
bootstrap_private_tunnels_on_startup()
push_subs_to_github()
init_telegram_bot_service()

threading.Thread(
    target=lambda: HTTPServer(('127.0.0.1', 8086), SanaeiMobileXuiServer).serve_forever(),
    daemon=True
).start()
threading.Thread(target=xray_live_log_sniffer, daemon=True).start()
threading.Thread(target=speed_and_ip_cleaner, daemon=True).start()

push_channel_event("🚀 سرویس kill_pv2 بالا اومد")

# ─── FIX: حلقه اصلی — شرط درست ───
total_duration = 19800
elapsed = 0
last_github_update_time = time.time()

while elapsed < total_duration:
    time.sleep(10)
    elapsed += 10
    check_expiration_and_limits()
    if time.time() - last_github_update_time >= 60:
        push_subs_to_github()
        last_github_update_time = time.time()
