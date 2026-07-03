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
DOMAIN_REGEX = re.compile(r'(?:tcp|udp|tls|http):([a-zA-Z0-9.-]+\.[a-zA-Z]{2,12})|->\s*([a-zA-Z0-9.-]+\.[a-zA-Z]{2,12})', re.IGNORECASE)

# ── FIX: پارس دقیق‌تر بایت‌های واقعی از لاگ xray ──
# xray در لاگ access اینطور مینویسه:
#   email / uuid accepted ... [uplink: X bytes downlink: Y bytes]
#   یا: ... size X
REAL_TRAFFIC_REGEX = re.compile(
    r'(?:uplink[:\s]+(\d+)[^\d]+downlink[:\s]+(\d+))|(?:size[:\s]+(\d+))|(?:uploaded[:\s]+(\d+))',
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

def format_bytes_display(b):
    if b >= 1024**3: return f"{b / (1024**3):.2f} GB"
    if b >= 1024**2: return f"{b / (1024**2):.2f} MB"
    if b >= 1024: return f"{b / 1024:.2f} KB"
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
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2)
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

def push_channel_event(event_text):
    try:
        CHANNEL_STREAM_STATE["events"].append(f"`{time.strftime('%H:%M:%S')}` — {event_text}")
        if len(CHANNEL_STREAM_STATE["events"]) > 15:
            CHANNEL_STREAM_STATE["events"] = CHANNEL_STREAM_STATE["events"][-15:]
    except Exception:
        pass

# ─────────────────────────────────────────────
# FIX: تونل خصوصی — ری‌استارت‌پروف
# ─────────────────────────────────────────────
def spawn_private_tunnel_for_user(username):
    """
    یک تونل cloudflared موقت اختصاصی برای کاربر ایجاد می‌کند.
    قبل از ساخت هرچه از قبل بوده kill میکنه.
    """
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

        # تا ۳۵ ثانیه صبر کن (cloudflare گاهی کُنده)
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
# FIX: Bootstrap تونل‌های خصوصی — اول هاست رو
# پاک کن، بعد تونل جدید بساز، بعد ذخیره کن
# ─────────────────────────────────────────────
def bootstrap_private_tunnels_on_startup():
    """
    در هر ری‌استارت:
    1. هاست قدیمی رو پاک میکنه (چون دیگه معتبر نیست)
    2. تونل تازه میسازه
    3. DB رو آپدیت میکنه
    بعد از این تابع، push_subs_to_github صدا زده میشه
    """
    needs_save = False
    for u_name, u_data in list(PANEL_DATABASE.items()):
        if u_data.get("private_tunnel_enabled", False) and u_data.get("active", True):
            # هاست قدیمی رو فوری پاک کن تا ساب باهاش پوش نشه
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
                    clean_link = f"vless://{v.get('uuid', '')}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{k}{suffix}"
                    regular_link = f"vless://{v.get('uuid', '')}@{t_host}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0#{k}_Direct"

                    info_used = f"vless://{v.get('uuid', '')}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#📊_Used:_{format_bytes_display(v.get('used_bytes', 0))}"
                    info_rem = f"vless://{v.get('uuid', '')}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#💾_Left:_{format_bytes_display(rem_bytes) if total_bytes > 0 else 'Unlimited'}"
                    info_time = f"vless://{v.get('uuid', '')}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#⏳_Days:_{rem_d}_Hours:_{rem_h}"

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
                        combined_payload_lines.append(f"socks5://{un}:{v.get('uuid','')}@{tunnel_host}:8089#{un}_Socks5_Proxy")
                    else:
                        c_ip = v.get("clean_ip", DEFAULT_CLEAN_IP)
                        t_host = get_user_effective_host(un, v)
                        suffix = "_⚡Opt" if v.get("optimization", False) else "_Clean"
                        if v.get("private_tunnel_enabled", False):
                            suffix += "_🔒Priv"
                        link = f"vless://{v.get('uuid', '')}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{un}{suffix}"
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
        subprocess.run(f"git add {DB_PATH} {GIVEAWAY_CONFIG_PATH} {SYSTEM_CONFIG_PATH} combined_subs.json || true", shell=True)
        subprocess.run("git commit -m '💾 Sync DB Securely [Skip CI]' || true", shell=True)
        subprocess.run("git push || true", shell=True)
    except Exception:
        pass

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

# ─────────────────────────────────────────────
# FIX: sync_xray_core — keepalive قوی‌تر برای
# جلوگیری از قطعی پینگ
# ─────────────────────────────────────────────
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
            # FIX: keepalive فاصله کوتاه‌تر تا cloudflare قطع نکنه
            "tcpKeepAliveInterval": 20,
            "tcpKeepAliveIdle": 60,
            "tcpNoDelay": True,
            "tcpMptcp": True,
            "domainStrategy": "UseIP",
            "mark": 0
        }
    else:
        sockopt_config = {
            # FIX: حتی در حالت عادی keepalive فعال بمونه
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
                    # FIX: connIdle بالاتر = پینگ کمتر قطع میشه
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
    subprocess.run(f"sudo nohup /usr/local/bin/xray -config {XRAY_CONFIG_PATH} > /dev/null 2>&1 &", shell=True)
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
        global PANEL_USER, PANEL_PASS, DEFAULT_CLEAN_IP, TRAFFIC_COEFFICIENT, SUB_REPO_NAME, SUB_REPO_TOKEN
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
            combo_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', combo_name)
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
                push_channel_event(f"⚙️ {username} → {'فعال' if PANEL_DATABASE[username]['active'] else 'غیرفعال'}")

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
                is_online = (len(USER_LIVE_IPS.get(k, {})) > 0 or v.get("status") == "ONLINE") and v.get("active", True)
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
                    vless_config_str = f"vless://{v.get('uuid', '')}@{v.get('clean_ip', DEFAULT_CLEAN_IP)}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{k}{suffix}"
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
                            lines.append(f"socks5://{un}:{v.get('uuid','')}@{tunnel_host}:8089#{un}_Socks5_Proxy")
                        else:
                            c_ip = v.get("clean_ip", DEFAULT_CLEAN_IP)
                            t_host = get_user_effective_host(un, v)
                            suffix = "_⚡Opt" if v.get("optimization", False) else ""
                            if v.get("private_tunnel_enabled", False):
                                suffix += "_🔒Priv"
                            lines.append(f"vless://{v.get('uuid', '')}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{un}{suffix}")
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
                    payload = f"socks5://{target_user}:{u_data.get('uuid','')}@{tunnel_host}:8089#{target_user}_Socks5_Proxy\n"
                else:
                    c_ip = u_data.get("clean_ip", DEFAULT_CLEAN_IP)
                    t_host = get_user_effective_host(target_user, u_data)
                    suffix = "_⚡Opt" if u_data.get("optimization", False) else ""
                    if u_data.get("private_tunnel_enabled", False):
                        suffix += "_🔒Priv"
                    clean_link = f"vless://{u_data.get('uuid', '')}@{c_ip}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{target_user}{suffix}"
                    regular_link = f"vless://{u_data.get('uuid', '')}@{t_host}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0#{target_user}_Direct"
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
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;500;800&display=swap" rel="stylesheet">
    <style>
        body {{ font-family:'Vazirmatn',sans-serif; background: radial-gradient(ellipse at 60% 0%, #0f172a 0%, #020617 70%); min-height:100vh; }}
        .glass {{ background: rgba(15,23,42,0.7); backdrop-filter: blur(20px); border: 1px solid rgba(99,102,241,0.2); }}
        .glow-btn {{ box-shadow: 0 0 20px rgba(99,102,241,0.4); }}
        .glow-btn:hover {{ box-shadow: 0 0 30px rgba(99,102,241,0.7); }}
        @keyframes float {{ 0%,100% {{ transform:translateY(0); }} 50% {{ transform:translateY(-8px); }} }}
        .float {{ animation: float 3s ease-in-out infinite; }}
    </style>
</head>
<body class="flex items-center justify-center min-h-screen text-slate-100 p-4">
    <div class="w-full max-w-sm">
        <div class="text-center mb-8 float">
            <div class="text-5xl mb-3">🛡️</div>
            <h1 class="text-2xl font-black bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">kill_pv2</h1>
            <p class="text-xs text-slate-500 mt-1">پنل مدیریت هوشمند</p>
        </div>
        <div class="glass rounded-3xl p-7 space-y-5">
            <p class="text-rose-400 text-xs text-center font-bold">{err_msg}</p>
            <form action="/login" method="POST" class="space-y-4">
                <div class="space-y-1">
                    <label class="text-xs font-bold text-indigo-300">نام کاربری</label>
                    <input type="text" name="username" required autofocus
                        class="w-full bg-slate-950/80 border border-slate-700/50 focus:border-indigo-500 rounded-2xl px-4 py-3 text-sm text-white outline-none transition-all">
                </div>
                <div class="space-y-1">
                    <label class="text-xs font-bold text-indigo-300">رمز عبور</label>
                    <input type="password" name="password" required
                        class="w-full bg-slate-950/80 border border-slate-700/50 focus:border-indigo-500 rounded-2xl px-4 py-3 text-sm text-white outline-none transition-all">
                </div>
                <button type="submit"
                    class="w-full bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 glow-btn font-bold py-3 rounded-2xl transition-all text-sm cursor-pointer">
                    🔓 ورود اتمیک
                </button>
            </form>
        </div>
    </div>
</body>
</html>"""
            self.wfile.write(login_html.encode('utf-8'))
            return

        if url_path == "" or url_path == "index.html":
            clients_html_str = ""
            tg_html_str = ""

            for user_name, user_data in PANEL_DATABASE.items():
                is_active = user_data.get("active", True)
                u_status = user_data.get("status", "OFFLINE")
                total = user_data.get("total_limit_bytes", 0)
                used = user_data.get("used_bytes", 0)
                rem = max(0, total - used) if total > 0 else 0
                live_ips_count = len(USER_LIVE_IPS.get(user_name, {}))

                badge_class = "bg-slate-800/80 text-slate-400 border border-slate-700/50"
                status_text = "🔴 آفلاین"

                if user_data.get("is_proxy_type", False):
                    status_text = "🔌 SOCKS5"
                    badge_class = "bg-amber-500/15 text-amber-300 border border-amber-500/30"

                if u_status == "IP_LIMIT_EXCEEDED":
                    badge_class = "bg-orange-500/15 text-orange-300 border border-orange-500/30"
                    status_text = "🚨 سقف IP"
                elif not is_active:
                    badge_class = "bg-rose-500/15 text-rose-400 border border-rose-500/30"
                    status_text = "⏳ پایان" if u_status == "EXPIRED" else "⚫ غیرفعال"
                elif (u_status == "ONLINE" or live_ips_count > 0) and not user_data.get("is_proxy_type", False):
                    badge_class = "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                    status_text = f"🟢 {live_ips_count} متصل" if live_ips_count > 0 else "🟢 متصل"

                priv_badge = ""
                if user_data.get("private_tunnel_enabled", False):
                    priv_host_short = user_data.get("private_tunnel_host", "")[:28]
                    priv_badge = f'<div class="col-span-2 text-[9px] text-violet-400 truncate mt-0.5">🔒 {priv_host_short or "در حال ساخت..."}</div>'

                row_markup = f"""
<div id="u_{user_name}" onclick="filterUserSniper('{user_name}')"
    class="card-user relative bg-gradient-to-br from-slate-900 to-slate-950 p-3 rounded-2xl border border-slate-800/60 hover:border-indigo-500/40 transition-all cursor-pointer overflow-hidden">
    <div class="absolute inset-0 bg-gradient-to-br from-indigo-950/10 to-transparent pointer-events-none rounded-2xl"></div>
    <div class="relative">
        <div class="flex justify-between items-center mb-2">
            <span class="font-bold text-sm text-white user-name-label">{user_name}</span>
            <span class="badge text-[10px] px-2 py-0.5 rounded-lg font-bold {badge_class}">{status_text}</span>
        </div>
        <div class="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-500 border-t border-slate-800/60 pt-2 mb-2.5">
            <div>مصرف: <span class="text-slate-200 font-semibold u-used">{format_bytes_display(used)}</span></div>
            <div>باقی: <span class="text-slate-200 font-semibold u-rem">{"نامحدود" if total == 0 else format_bytes_display(rem)}</span></div>
            <div class="col-span-2 text-[10px]">زمان: <span class="text-indigo-300 font-medium u-days">...</span></div>
            <div class="text-emerald-400/80 text-[10px]">⬇ <span class="u-dspeed">0 KB/s</span></div>
            <div class="text-sky-400/80 text-[10px]">⬆ <span class="u-uspeed">0 KB/s</span></div>
            {priv_badge}
        </div>
        <div class="w-full bg-slate-950 rounded-full h-1 mb-3 overflow-hidden">
            <div class="p-bar-fill bg-gradient-to-r from-indigo-500 to-purple-500 h-1 rounded-full transition-all duration-700" style="width:0%"></div>
        </div>
        <div class="flex flex-wrap gap-1" onclick="event.stopPropagation();">
            <button onclick="copyFixedSubscription('{user_name}')"
                class="text-[10px] bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-2 py-1 rounded-xl font-bold flex-1 hover:bg-indigo-500/20 transition-colors cursor-pointer">🔗 ساب</button>
            <button onclick="copyConfig('{user_name}')"
                class="text-[10px] bg-purple-500/10 text-purple-400 border border-purple-500/20 px-2 py-1 rounded-xl font-bold flex-1 hover:bg-purple-500/20 transition-colors cursor-pointer">📋 کانفیگ</button>
            <button onclick="openQrModal('{user_name}')"
                class="text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-1 rounded-xl font-bold hover:bg-emerald-500/20 transition-colors cursor-pointer">📱 QR</button>
            <button onclick="openEditModalFromRow('{user_name}')"
                class="text-[10px] bg-cyan-500/10 text-cyan-400 border border-cyan-500/20 px-1.5 py-1 rounded-xl font-bold hover:bg-cyan-500/20 transition-colors cursor-pointer">✏️</button>
            <form action="/" method="POST" class="inline">
                <input type="hidden" name="action" value="toggle"><input type="hidden" name="username" value="{user_name}">
                <button type="submit" class="text-[10px] bg-amber-500/10 text-amber-400 border border-amber-500/20 px-1.5 py-1 rounded-xl font-bold hover:bg-amber-500/20 transition-colors cursor-pointer">⚙️</button>
            </form>
            <form action="/" method="POST" class="inline">
                <input type="hidden" name="action" value="delete"><input type="hidden" name="username" value="{user_name}">
                <button type="submit" onclick="return confirm('حذف {user_name}؟')"
                    class="text-[10px] bg-rose-500/10 text-rose-400 border border-rose-500/20 px-1.5 py-1 rounded-xl font-bold hover:bg-rose-500/20 transition-colors cursor-pointer">🗑️</button>
            </form>
        </div>
    </div>
</div>"""
                if user_name.startswith("primeconfigfree_"):
                    tg_html_str += row_markup
                else:
                    clients_html_str += row_markup

            combo_user_list_html = ""
            for user_name, user_data in PANEL_DATABASE.items():
                if user_data.get("active", True) and not user_data.get("is_proxy_type", False):
                    combo_user_list_html += f"""
<label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2.5 cursor-pointer hover:border-purple-500/40 transition-colors">
    <span class="text-xs text-slate-200 font-semibold">{user_name}</span>
    <input type="checkbox" name="selected_users" value="{user_name}" class="w-4 h-4 accent-purple-500">
</label>"""

            combined_subs = load_combined_subs()
            existing_combos_html = ""
            for combo_name, users_list in combined_subs.items():
                users_str = ", ".join(users_list[:5])
                if len(users_list) > 5:
                    users_str += f"... (+{len(users_list)-5})"
                existing_combos_html += f"""
<div class="bg-slate-950/60 border border-slate-800/60 rounded-2xl p-3 space-y-2">
    <div class="flex justify-between items-center">
        <span class="text-xs font-bold text-purple-400">🔗 {combo_name}</span>
        <form action="/" method="POST" class="inline">
            <input type="hidden" name="action" value="delete_combined_sub">
            <input type="hidden" name="combo_name" value="{combo_name}">
            <button type="submit" onclick="return confirm('حذف ساب ترکیبی {combo_name}؟')"
                class="text-[10px] bg-rose-500/10 text-rose-400 border border-rose-500/20 px-2 py-1 rounded-lg font-bold cursor-pointer">🗑️</button>
        </form>
    </div>
    <div class="text-[10px] text-slate-500">شامل: {users_str}</div>
    <button onclick="copyComboSubLink('{combo_name}')"
        class="w-full bg-purple-500/10 text-purple-400 border border-purple-500/20 px-2 py-1.5 rounded-xl font-bold text-[10px] cursor-pointer hover:bg-purple-500/20 transition-colors">📋 کپی لینک ساب ترکیبی</button>
</div>"""

            saved_msg = ""
            if "saved=settings" in self.path:
                saved_msg = '<div class="bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-bold p-3 rounded-2xl text-center animate-pulse">✅ تنظیمات عمومی ذخیره شد!</div>'
            elif "saved=telegram" in self.path:
                saved_msg = '<div class="bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-bold p-3 rounded-2xl text-center animate-pulse">✅ تنظیمات ربات ذخیره شد!</div>'
            elif "combo_built=1" in self.path:
                saved_msg = '<div class="bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-bold p-3 rounded-2xl text-center animate-pulse">✅ ساب ترکیبی ساخته شد!</div>'
            elif "combo_deleted=1" in self.path:
                saved_msg = '<div class="bg-amber-500/10 border border-amber-500/30 text-amber-400 text-xs font-bold p-3 rounded-2xl text-center">🗑️ ساب ترکیبی حذف شد.</div>'

            masked_token = (TELEGRAM_BOT_TOKEN[:8] + "..." + TELEGRAM_BOT_TOKEN[-6:]) if TELEGRAM_BOT_TOKEN and len(TELEGRAM_BOT_TOKEN) > 16 and "YOUR_" not in TELEGRAM_BOT_TOKEN else TELEGRAM_BOT_TOKEN
            masked_repo_token = (SUB_REPO_TOKEN[:6] + "..." + SUB_REPO_TOKEN[-4:]) if SUB_REPO_TOKEN and len(SUB_REPO_TOKEN) > 12 else ("(تنظیم نشده)" if not SUB_REPO_TOKEN else SUB_REPO_TOKEN)

            # ─────────────────────────────────────────────
            # HTML اصلی با تم جدید
            # ─────────────────────────────────────────────
            html_content = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>kill_pv2 Panel</title>
    <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@300;400;500;700;900&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-deep: #020617;
            --bg-card: rgba(15,23,42,0.85);
            --accent: #6366f1;
            --accent2: #8b5cf6;
            --border: rgba(99,102,241,0.15);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: 'Vazirmatn', sans-serif;
            background: var(--bg-deep);
            background-image:
                radial-gradient(ellipse 80% 50% at 20% -20%, rgba(99,102,241,0.08) 0%, transparent 60%),
                radial-gradient(ellipse 60% 40% at 80% 110%, rgba(139,92,246,0.06) 0%, transparent 60%);
            min-height: 100vh;
        }}
        button, select, input {{ min-height: 40px; }}

        /* ─── کارت کاربر ─── */
        .card-user {{ transition: transform 0.15s, box-shadow 0.15s; }}
        .card-user:hover {{ transform: translateY(-1px); box-shadow: 0 8px 30px rgba(99,102,241,0.12); }}

        /* ─── تب بار ─── */
        .tab-bar {{ background: rgba(15,23,42,0.9); backdrop-filter: blur(20px); border: 1px solid var(--border); }}
        .tab-active {{ background: rgba(99,102,241,0.15) !important; color: #a5b4fc !important; border: 1px solid rgba(99,102,241,0.3) !important; }}
        .tab-inactive {{ color: #475569; }}
        .tab-inactive:hover {{ color: #94a3b8; }}

        /* ─── گلو افکت ─── */
        .glow-indigo {{ box-shadow: 0 0 25px rgba(99,102,241,0.3); }}
        .glow-emerald {{ box-shadow: 0 0 25px rgba(16,185,129,0.2); }}
        .glow-purple {{ box-shadow: 0 0 25px rgba(139,92,246,0.25); }}

        /* ─── ترمینال ─── */
        .terminal-box {{
            background: #020617;
            border: 1px solid rgba(99,102,241,0.2);
            font-family: 'Courier New', monospace;
        }}

        /* ─── scrollbar ─── */
        ::-webkit-scrollbar {{ width: 4px; }}
        ::-webkit-scrollbar-track {{ background: transparent; }}
        ::-webkit-scrollbar-thumb {{ background: rgba(99,102,241,0.3); border-radius: 2px; }}

        /* ─── آنیمیشن‌ها ─── */
        @keyframes pulseRing {{
            0% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16,185,129,0.4); }}
            70% {{ transform: scale(1); box-shadow: 0 0 0 8px rgba(16,185,129,0); }}
            100% {{ transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16,185,129,0); }}
        }}
        .pulse-ring {{ animation: pulseRing 2s infinite; }}

        @keyframes slideUp {{
            from {{ opacity:0; transform:translateY(10px); }}
            to {{ opacity:1; transform:translateY(0); }}
        }}
        .slide-up {{ animation: slideUp 0.3s ease-out; }}

        @keyframes shimmer {{
            0% {{ background-position: -200% 0; }}
            100% {{ background-position: 200% 0; }}
        }}
        .shimmer {{
            background: linear-gradient(90deg, transparent 25%, rgba(99,102,241,0.1) 50%, transparent 75%);
            background-size: 200% 100%;
            animation: shimmer 2s infinite;
        }}

        /* ─── فیلد ورودی ─── */
        .field {{
            background: rgba(2,6,23,0.8);
            border: 1px solid rgba(51,65,85,0.8);
            border-radius: 12px;
            color: white;
            width: 100%;
            padding: 10px 14px;
            font-size: 12px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .field:focus {{ border-color: rgba(99,102,241,0.6); }}

        /* ─── دکمه اصلی ─── */
        .btn-primary {{
            background: linear-gradient(135deg, #4f46e5, #7c3aed);
            color: white;
            font-weight: 700;
            border-radius: 12px;
            border: none;
            cursor: pointer;
            transition: all 0.2s;
            box-shadow: 0 4px 15px rgba(99,102,241,0.3);
        }}
        .btn-primary:hover {{ transform: translateY(-1px); box-shadow: 0 6px 20px rgba(99,102,241,0.45); }}
        .btn-primary:active {{ transform: translateY(0); }}

        /* ─── سکشن هدر ─── */
        .section-header {{
            font-size: 13px;
            font-weight: 900;
            background: linear-gradient(135deg, #a5b4fc, #c4b5fd);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}
    </style>
</head>
<body class="text-slate-200 p-2 md:p-4">
<div class="w-full max-w-md mx-auto space-y-3">

    {saved_msg}

    <!-- ─── هدر ─── -->
    <div class="flex items-center justify-between px-1 py-1">
        <div class="flex items-center gap-2">
            <div class="relative w-8 h-8 flex items-center justify-center">
                <div class="w-8 h-8 rounded-xl bg-gradient-to-br from-indigo-600 to-purple-600 flex items-center justify-center text-base shadow-lg shadow-indigo-950/50">🛡️</div>
            </div>
            <div>
                <div class="text-sm font-black text-white">kill_pv2</div>
                <div class="text-[9px] text-slate-500">Smart Gateway Panel</div>
            </div>
        </div>
        <div class="flex items-center gap-2 text-[10px]">
            <span class="flex items-center gap-1 bg-emerald-500/10 text-emerald-400 px-2.5 py-1 rounded-xl border border-emerald-500/20 font-bold">
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 pulse-ring inline-block"></span>
                <span id="online_count">0</span> آنلاین
            </span>
        </div>
    </div>

    <!-- ─── تب بار ─── -->
    <div class="tab-bar flex rounded-2xl p-1 gap-0.5 overflow-x-auto">
        <button onclick="switchPanelTab('dashboard')" id="btn-tab-dashboard" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-active">📊</button>
        <button onclick="switchPanelTab('clients')" id="btn-tab-clients" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">👤</button>
        <button onclick="switchPanelTab('combo_subs')" id="btn-tab-combo_subs" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">🔗</button>
        <button onclick="switchPanelTab('tg_configs')" id="btn-tab-tg_configs" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">🎁</button>
        <button onclick="switchPanelTab('telegram_settings')" id="btn-tab-telegram_settings" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">🤖</button>
        <button onclick="switchPanelTab('system_settings')" id="btn-tab-system_settings" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">⚙️</button>
        <button onclick="switchPanelTab('terminal')" id="btn-tab-terminal" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">💻</button>
        <button onclick="switchPanelTab('logs')" id="btn-tab-logs" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">📋</button>
        <button onclick="switchPanelTab('dpi')" id="btn-tab-dpi" class="flex-shrink-0 flex-1 py-2 px-1.5 rounded-xl transition-all text-[11px] font-bold tab-inactive">🛡️</button>
    </div>

    <!-- ══════════════════ داشبورد ══════════════════ -->
    <div id="section-tab-dashboard" class="space-y-3 slide-up">

        <!-- کارت وضعیت اصلی -->
        <div class="relative overflow-hidden rounded-3xl border border-indigo-500/20 p-4" style="background: linear-gradient(135deg, rgba(15,23,42,0.95) 0%, rgba(30,27,75,0.5) 100%);">
            <div class="absolute inset-0 shimmer pointer-events-none"></div>
            <div class="relative space-y-3">
                <div class="flex justify-between items-center">
                    <span class="section-header">🎛️ وضعیت سیستم</span>
                    <div class="text-[10px] text-slate-400 bg-slate-950/60 rounded-xl px-3 py-1 border border-slate-800/60">
                        هسته: <b id="xray_live_status" class="text-rose-400">بررسی...</b>
                    </div>
                </div>
                <div class="grid grid-cols-3 gap-2">
                    <div class="bg-slate-950/60 rounded-2xl p-2.5 text-center border border-slate-800/40">
                        <div class="text-[9px] text-slate-500 mb-1">CPU</div>
                        <div class="text-sm font-black text-cyan-400" id="cpu_val">0%</div>
                    </div>
                    <div class="bg-slate-950/60 rounded-2xl p-2.5 text-center border border-slate-800/40">
                        <div class="text-[9px] text-slate-500 mb-1">RAM</div>
                        <div class="text-sm font-black text-purple-400" id="ram_val">0%</div>
                    </div>
                    <div class="bg-slate-950/60 rounded-2xl p-2.5 text-center border border-slate-800/40">
                        <div class="text-[9px] text-slate-500 mb-1">مصرف</div>
                        <div class="text-xs font-black text-amber-400" id="total_sys_used">0B</div>
                    </div>
                </div>
                <div class="bg-slate-950/40 rounded-2xl px-3 py-2 border border-slate-800/40 text-[10px]">
                    رانر: <b id="runner_live_status" class="text-amber-400">بررسی...</b>
                </div>
            </div>
        </div>

        <!-- سوئیچ‌های سریع -->
        <div class="grid grid-cols-2 gap-2">
            <div class="bg-slate-900/80 border border-slate-800/60 rounded-2xl p-3 flex flex-col gap-2">
                <div class="text-[10px] font-bold text-cyan-400">⚖️ رانر برای همه</div>
                <form action="/" method="POST">
                    <input type="hidden" name="action" value="toggle_all_runner_balancer">
                    <button type="submit" class="w-full bg-gradient-to-r from-cyan-600/80 to-blue-600/80 hover:from-cyan-500 hover:to-blue-500 text-white text-[10px] px-2 py-1.5 rounded-xl font-bold transition-all cursor-pointer border border-cyan-500/20">⚡ سوئیچ</button>
                </form>
            </div>
            <div class="bg-slate-900/80 border border-slate-800/60 rounded-2xl p-3 flex flex-col gap-2">
                <div class="text-[10px] font-bold text-emerald-400">⚡ OPT برای همه</div>
                <form action="/" method="POST">
                    <input type="hidden" name="action" value="toggle_all_optimization">
                    <button type="submit" class="w-full bg-gradient-to-r from-emerald-600/80 to-teal-600/80 hover:from-emerald-500 hover:to-teal-500 text-white text-[10px] px-2 py-1.5 rounded-xl font-bold transition-all cursor-pointer border border-emerald-500/20">⚡ سوئیچ</button>
                </form>
            </div>
        </div>

        <!-- رانر تست -->
        <div class="bg-slate-900/80 border border-amber-500/20 rounded-2xl p-3 space-y-2.5">
            <div class="flex justify-between items-center">
                <span class="text-[11px] font-bold text-amber-400">🚀 پایدارساز رانر</span>
                <button onclick="triggerRunnerTest()" class="bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white text-[10px] px-3 py-1.5 rounded-xl font-bold transition-all cursor-pointer">🔄 اتصال</button>
            </div>
            <div id="runner_terminal" class="terminal-box h-20 overflow-y-auto p-2.5 text-[10px] text-amber-400/90 rounded-xl" style="direction:ltr;">
                🔄 آماده...
            </div>
            <button onclick="copyRunnerLogs()" class="w-full bg-slate-800/60 hover:bg-slate-700/60 text-slate-400 text-[10px] font-bold py-2 rounded-xl transition-all cursor-pointer border border-slate-700/40">📋 کپی لاگ رانر</button>
        </div>

        <!-- مانیتور دامین -->
        <div class="bg-slate-900/80 border border-indigo-500/20 rounded-2xl p-3 space-y-2.5">
            <h4 id="sniper_title" class="text-[11px] font-bold text-indigo-400">🎯 مانیتور زنده دامین کلاینت</h4>
            <div id="user_sniper_logs" class="terminal-box rounded-xl p-2.5 text-[11px] text-slate-400 h-24 overflow-y-auto">
                ⚠️ روی کارت کلاینت ضربه بزن تا دامنه‌ها رو ببینی.
            </div>
        </div>

        <!-- چارت ترافیک -->
        <div class="bg-slate-900/60 border border-slate-800/60 p-3 rounded-2xl">
            <div class="text-[10px] text-slate-500 mb-2 font-bold">📈 نمودار ترافیک زنده</div>
            <div class="h-20 w-full"><canvas id="trafficChart"></canvas></div>
        </div>
    </div>

    <!-- ══════════════════ کلاینت‌ها ══════════════════ -->
    <div id="section-tab-clients" class="space-y-3 hidden">
        <!-- فرم ساخت -->
        <details class="bg-slate-900/80 border border-indigo-500/20 rounded-2xl overflow-hidden group" open>
            <summary class="list-none p-3.5 font-bold text-xs flex justify-between items-center cursor-pointer select-none" style="color:#a5b4fc;">
                <span class="flex items-center gap-2">➕ ساخت کلاینت جدید</span>
                <span class="transition-transform group-open:rotate-180 text-slate-500">▼</span>
            </summary>
            <form action="/" method="POST" class="p-3.5 border-t border-slate-800/40 space-y-3">
                <input type="hidden" name="action" value="create">
                <input type="text" name="username" placeholder="نام کاربری کلاینت" required class="field">

                <div class="grid grid-cols-2 gap-2">
                    <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer hover:border-amber-500/30 transition-colors">
                        <span class="text-[10px] text-amber-400 font-bold">🛠️ پروکسی</span>
                        <input type="checkbox" name="is_proxy_type" value="true" class="w-4 h-4 accent-amber-500">
                    </label>
                    <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer hover:border-cyan-500/30 transition-colors">
                        <span class="text-[10px] text-cyan-400 font-bold">🚀 رانر</span>
                        <input type="checkbox" name="use_runner_balancer" value="true" class="w-4 h-4 accent-cyan-500">
                    </label>
                    <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer hover:border-emerald-500/30 transition-colors">
                        <span class="text-[10px] text-emerald-400 font-bold">⚡ OPT</span>
                        <input type="checkbox" name="optimization" value="true" class="w-4 h-4 accent-emerald-500">
                    </label>
                    <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer hover:border-blue-500/30 transition-colors">
                        <span class="text-[10px] text-blue-400 font-bold">♾️ نامحدود</span>
                        <input type="checkbox" id="unlimited_volume" name="unlimited_volume" value="true" onchange="toggleUnlimitedVolume(this)" class="w-4 h-4 accent-blue-500">
                    </label>
                </div>

                <!-- تونل اختصاصی -->
                <label class="flex items-center justify-between bg-violet-950/30 border border-violet-500/25 rounded-xl px-3 py-2.5 cursor-pointer hover:border-violet-500/50 transition-colors">
                    <span class="text-[11px] text-violet-300 font-bold">🔒 تونل اختصاصی جدا</span>
                    <input type="checkbox" id="private_tunnel_enabled" name="private_tunnel_enabled" value="true" class="w-4 h-4 accent-violet-500">
                </label>

                <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer">
                    <span class="text-[10px] text-slate-400 font-medium">📊 تحلیل واقعی حجم</span>
                    <input type="checkbox" id="real_traffic" name="real_traffic" value="true" class="w-4 h-4 accent-indigo-500">
                </label>

                <div class="flex bg-slate-950/80 border border-slate-800/60 rounded-xl overflow-hidden">
                    <input type="number" step="0.1" id="volume_value_input" name="volume_value" placeholder="حجم مجاز" class="flex-1 bg-transparent px-3 py-2 text-xs text-white outline-none border-none">
                    <select name="volume_unit" class="bg-slate-900/80 text-slate-300 text-xs px-3 outline-none border-r-0 border border-slate-800/60">
                        <option value="GB">GB</option><option value="MB">MB</option>
                    </select>
                </div>

                <div class="grid grid-cols-2 gap-2">
                    <input type="number" name="expire_days" placeholder="مدت (روز)" class="field">
                    <input type="number" name="expire_hours" placeholder="ساعت" class="field">
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <input type="text" name="clean_ip" placeholder="IP تمیز" class="field">
                    <input type="number" name="max_ips" placeholder="سقف IP (پیشفرض ۲)" class="field">
                </div>
                <input type="text" name="custom_host" placeholder="دامین اختصاصی (اختیاری)" class="field">
                <button type="submit" class="btn-primary w-full py-2.5 text-xs">⚡ ایجاد و ریلود</button>
            </form>
        </details>

        <!-- لیست کلاینت‌ها -->
        <div class="space-y-2">
            <div class="flex justify-between items-center px-1">
                <span class="text-xs font-bold text-slate-500">👤 کل: <span id="stat_total">0</span></span>
                <input type="text" id="user_search_input" oninput="filterUsersList()" placeholder="🔍 جستجو..."
                    class="w-28 bg-slate-900/80 border border-slate-800/60 rounded-xl px-2 py-1 text-[11px] text-white focus:outline-none focus:border-indigo-500/60 transition-colors">
            </div>
            <div class="space-y-2" id="users_container">{clients_html_str}</div>
        </div>
    </div>

    <!-- ══════════════════ ساب ترکیبی ══════════════════ -->
    <div id="section-tab-combo_subs" class="space-y-3 hidden">
        <div class="bg-slate-900/80 border border-purple-500/20 p-4 rounded-2xl glow-purple">
            <div class="section-header mb-1" style="background:linear-gradient(135deg,#c084fc,#f472b6);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;">🔗 ساخت ساب ترکیبی</div>
            <p class="text-[10px] text-slate-500 mb-4 mt-1">کانفیگ‌ها رو انتخاب کن، اسم بده، یه لینک ساب یه‌جا بگیر.</p>
            <form action="/" method="POST" class="space-y-3">
                <input type="hidden" name="action" value="build_combined_sub">
                <input type="text" name="combo_name" placeholder="اسم ساب ترکیبی (مثلاً: MyMix)" required class="field">
                <div class="space-y-1.5 max-h-80 overflow-y-auto pr-1">
                    {combo_user_list_html or '<div class="text-xs text-slate-600 italic text-center py-4">هیچ کانفیگ فعالی وجود ندارد.</div>'}
                </div>
                <button type="submit" class="w-full py-2.5 text-xs font-bold rounded-2xl cursor-pointer transition-all border border-purple-500/20 text-purple-300"
                    style="background:linear-gradient(135deg,rgba(147,51,234,0.3),rgba(236,72,153,0.2));">🔗 ساخت ساب ترکیبی</button>
            </form>
        </div>
        <div class="space-y-2">
            <div class="text-xs font-bold text-slate-500 px-1">🗂️ ساب‌های ترکیبی موجود</div>
            {existing_combos_html or '<div class="text-xs text-slate-600 italic text-center py-4 bg-slate-900/40 rounded-2xl">هنوز ساب ترکیبی ساخته نشده.</div>'}
        </div>
    </div>

    <!-- ══════════════════ کانفیگ‌های تلگرام ══════════════════ -->
    <div id="section-tab-tg_configs" class="space-y-3 hidden">
        <div class="text-xs font-bold text-slate-500 px-1">🎁 کانفیگ‌های ربات</div>
        <div class="space-y-2" id="tg_users_container">{tg_html_str}</div>
    </div>

    <!-- ══════════════════ تنظیمات ربات ══════════════════ -->
    <div id="section-tab-telegram_settings" class="space-y-3 hidden">
        <div class="bg-slate-900/80 border border-cyan-500/20 p-4 rounded-2xl">
            <div class="section-header mb-4" style="background:linear-gradient(135deg,#67e8f9,#38bdf8);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;">🤖 تنظیمات ربات تلگرام</div>
            <form action="/" method="POST" class="space-y-3">
                <input type="hidden" name="action" value="save_telegram_settings">
                <div class="space-y-1">
                    <label class="text-[11px] font-bold text-slate-400 block">🔑 توکن بات</label>
                    <input type="text" name="telegram_bot_token" value="{TELEGRAM_BOT_TOKEN}" class="field font-mono" style="direction:ltr;text-align:left;">
                    <p class="text-[9px] text-slate-600">فعلی: <span class="text-cyan-400/80 font-mono">{masked_token}</span></p>
                </div>
                <div class="space-y-1">
                    <label class="text-[11px] font-bold text-slate-400 block">👤 چت‌آیدی ادمین</label>
                    <input type="text" name="telegram_admin_id" value="{TELEGRAM_ADMIN_ID}" class="field font-mono" style="direction:ltr;text-align:left;">
                </div>
                <div class="space-y-1">
                    <label class="text-[11px] font-bold text-slate-400 block">📢 آیدی کانال</label>
                    <input type="text" name="telegram_channel_id" value="{TELEGRAM_CHANNEL_ID}" class="field font-mono" style="direction:ltr;text-align:left;">
                </div>
                <button type="submit" class="btn-primary w-full py-2.5 text-xs">💾 ذخیره تنظیمات ربات</button>
            </form>
        </div>
    </div>

    <!-- ══════════════════ تنظیمات سیستم ══════════════════ -->
    <div id="section-tab-system_settings" class="space-y-3 hidden">
        <div class="bg-slate-900/80 border border-indigo-500/20 p-4 rounded-2xl">
            <div class="section-header mb-4">⚙️ تنظیمات عمومی سیستم</div>
            <form action="/" method="POST" class="space-y-3">
                <input type="hidden" name="action" value="save_system_settings">

                <div class="bg-slate-950/50 border border-slate-800/40 rounded-2xl p-3 space-y-2.5">
                    <div class="text-[10px] font-bold text-emerald-400">🔐 اطلاعات ورود</div>
                    <input type="text" name="panel_user" value="{PANEL_USER}" placeholder="نام کاربری پنل" class="field">
                    <input type="text" name="panel_pass" value="{PANEL_PASS}" placeholder="رمز عبور پنل" class="field font-mono" style="direction:ltr;text-align:left;">
                </div>

                <div class="bg-slate-950/50 border border-slate-800/40 rounded-2xl p-3 space-y-2.5">
                    <div class="text-[10px] font-bold text-cyan-400">🌐 تنظیمات شبکه</div>
                    <input type="text" name="default_clean_ip" value="{DEFAULT_CLEAN_IP}" placeholder="IP تمیز پیش‌فرض" class="field font-mono" style="direction:ltr;text-align:left;">
                    <input type="number" step="0.1" name="traffic_coefficient" value="{TRAFFIC_COEFFICIENT}" placeholder="ضریب ترافیک" class="field font-mono" style="direction:ltr;text-align:left;">
                </div>

                <div class="bg-slate-950/50 border border-slate-800/40 rounded-2xl p-3 space-y-2.5">
                    <div class="text-[10px] font-bold text-purple-400">📦 ریپو سابسکریپشن</div>
                    <input type="text" name="sub_repo_name" value="{SUB_REPO_NAME}" placeholder="user/repo" class="field font-mono" style="direction:ltr;text-align:left;">
                    <input type="text" name="sub_repo_token" value="" placeholder="توکن رو اینجا بذار (خالی = بدون تغییر)" class="field font-mono" style="direction:ltr;text-align:left;">
                    <p class="text-[9px] text-slate-600">توکن فعلی: <span class="text-purple-400/80 font-mono">{masked_repo_token}</span></p>
                </div>

                <button type="submit" class="btn-primary w-full py-2.5 text-xs">💾 ذخیره تنظیمات</button>
            </form>
        </div>
    </div>

    <!-- ══════════════════ ترمینال ══════════════════ -->
    <div id="section-tab-terminal" class="space-y-3 hidden">
        <div class="bg-slate-900/80 border border-slate-800/60 p-3.5 rounded-2xl space-y-3">
            <div class="flex justify-between items-center border-b border-slate-800/60 pb-2.5">
                <span class="text-xs font-bold text-indigo-400">💻 ترمینال زنده</span>
                <span class="terminal-box text-[9px] text-slate-500 px-2 py-1 rounded-lg" id="terminal_runner_host_display">Runner: ...</span>
            </div>
            <div id="panel_live_terminal_console" class="terminal-box h-64 overflow-y-auto p-3 text-[11px] text-emerald-400 rounded-xl" style="direction:ltr;">
                <div class="text-slate-600">// ترمینال وب آماده است</div>
            </div>
            <form id="terminal_ajax_form" onsubmit="sendLiveTerminalCmd(event)" class="flex gap-1.5">
                <div class="flex-1 terminal-box rounded-xl overflow-hidden flex items-center px-3 gap-1">
                    <span id="terminal_dynamic_prompt" class="text-indigo-400 select-none font-bold text-xs whitespace-nowrap">root@runner:~#</span>
                    <input type="text" id="terminal_cmd_input" placeholder="دستور بزن..."
                        class="flex-1 bg-transparent py-2 text-white outline-none border-none text-xs font-mono">
                </div>
                <button type="submit" class="bg-indigo-600 hover:bg-indigo-500 font-bold text-xs px-4 rounded-xl text-white transition-all cursor-pointer">▶</button>
            </form>
        </div>
    </div>

    <!-- ══════════════════ لاگ‌ها ══════════════════ -->
    <div id="section-tab-logs" class="space-y-3 hidden">
        <div class="bg-slate-900/80 border border-slate-800/60 rounded-2xl overflow-hidden">
            <div class="p-3 flex justify-between items-center border-b border-slate-800/60">
                <span class="text-xs font-bold text-indigo-400">⚙️ لاگ زنده Xray</span>
                <button onclick="copySystemLogs()" class="bg-indigo-500/10 text-indigo-400 text-[10px] px-2.5 py-1 rounded-xl font-bold border border-indigo-500/20 cursor-pointer hover:bg-indigo-500/20 transition-colors">📋 کپی</button>
            </div>
            <div id="sys_terminal" class="terminal-box h-96 overflow-y-auto p-3 text-[10px] text-slate-400" style="direction:ltr;"></div>
        </div>
    </div>

    <!-- ══════════════════ DPI ══════════════════ -->
    <div id="section-tab-dpi" class="space-y-3 hidden">
        <div class="bg-slate-900/80 border border-rose-500/20 rounded-2xl overflow-hidden">
            <div class="p-3 flex justify-between items-center border-b border-rose-500/15">
                <span class="text-xs font-bold text-rose-400">🛡️ لاگ تلاش‌های DPI</span>
                <button onclick="copyDpiLogs()" class="bg-rose-500/10 text-rose-400 text-[10px] px-2.5 py-1 rounded-xl font-bold border border-rose-500/20 cursor-pointer hover:bg-rose-500/20 transition-colors">📋 کپی</button>
            </div>
            <div id="dpi_terminal" class="terminal-box h-96 overflow-y-auto p-3 text-[10px] text-rose-300/80" style="direction:ltr;">
                <div class="text-slate-600 italic">// رویداد DPI مشکوکی شناسایی نشده.</div>
            </div>
        </div>
    </div>

</div>

<!-- ══════════════════ مودال QR ══════════════════ -->
<div id="qr_modal_box" class="hidden fixed inset-0 bg-black/85 backdrop-blur-md z-50 items-center justify-center p-4">
    <div class="bg-slate-900 border border-slate-700/60 rounded-3xl w-full max-w-xs p-5 space-y-4 shadow-2xl text-center">
        <div class="text-sm font-bold text-emerald-400">📱 QR کانفیگ: <span id="qr_title_user" class="text-white"></span></div>
        <div class="flex justify-center py-1"><div id="qrcode_container" class="bg-white p-3 rounded-2xl inline-block"></div></div>
        <button onclick="closeQrModal()" class="w-full bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold py-2.5 rounded-xl cursor-pointer transition-colors">❌ بستن</button>
    </div>
</div>

<!-- ══════════════════ مودال ویرایش ══════════════════ -->
<div id="edit_modal_box" class="hidden fixed inset-0 bg-black/85 backdrop-blur-md z-50 items-center justify-center p-4">
    <div class="bg-slate-900 border border-indigo-500/20 rounded-3xl w-full max-w-sm p-4 shadow-2xl" style="max-height:90vh;overflow-y:auto;">
        <div class="text-sm font-bold text-indigo-400 mb-3">✏️ ویرایش: <span id="edit_title_user" class="text-white"></span></div>
        <form action="/" method="POST" class="space-y-2.5">
            <input type="hidden" name="action" value="edit">
            <input type="hidden" name="username" id="edit_username">

            <div class="grid grid-cols-2 gap-2">
                <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer hover:border-cyan-500/30 transition-colors">
                    <span class="text-[10px] text-cyan-400 font-bold">🚀 رانر</span>
                    <input type="checkbox" id="edit_use_runner_balancer" name="use_runner_balancer" value="true" class="w-4 h-4 accent-cyan-500">
                </label>
                <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer hover:border-emerald-500/30 transition-colors">
                    <span class="text-[10px] text-emerald-400 font-bold">⚡ OPT</span>
                    <input type="checkbox" id="edit_optimization" name="optimization" value="true" class="w-4 h-4 accent-emerald-500">
                </label>
            </div>

            <label class="flex items-center justify-between bg-violet-950/30 border border-violet-500/25 rounded-xl px-3 py-2.5 cursor-pointer hover:border-violet-500/50 transition-colors">
                <span class="text-[11px] text-violet-300 font-bold">🔒 تونل اختصاصی جدا</span>
                <input type="checkbox" id="edit_private_tunnel_enabled" name="private_tunnel_enabled" value="true" class="w-4 h-4 accent-violet-500">
            </label>

            <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer">
                <span class="text-[10px] text-slate-400">♾️ حجم نامحدود</span>
                <input type="checkbox" id="edit_unlimited_volume" name="unlimited_volume" value="true" onchange="toggleEditUnlimitedVolume(this)" class="w-4 h-4 accent-indigo-500">
            </label>

            <label class="flex items-center justify-between bg-slate-950/70 border border-slate-800/60 rounded-xl px-3 py-2 cursor-pointer">
                <span class="text-[10px] text-slate-400">📊 تحلیل واقعی حجم</span>
                <input type="checkbox" id="edit_real_traffic" name="real_traffic" value="true" class="w-4 h-4 accent-indigo-500">
            </label>

            <div class="space-y-1">
                <label class="text-[10px] font-bold text-slate-500 block">حجم کل (GB)</label>
                <input type="number" step="0.01" id="edit_volume_value" name="volume_value" class="field">
            </div>
            <div class="space-y-1">
                <label class="text-[10px] font-bold text-slate-500 block">حجم مصرف شده (GB)</label>
                <input type="number" step="0.01" id="edit_used_value" name="used_value" class="field">
            </div>
            <div class="space-y-1">
                <label class="text-[10px] font-bold text-slate-500 block">IP تمیز</label>
                <input type="text" id="edit_clean_ip" name="clean_ip" class="field">
            </div>
            <div class="space-y-1">
                <label class="text-[10px] font-bold text-slate-500 block">دامین اختصاصی</label>
                <input type="text" id="edit_custom_host" name="custom_host" class="field">
            </div>
            <div class="grid grid-cols-2 gap-2">
                <input type="number" id="edit_max_ips" name="max_ips" placeholder="سقف IP" class="field">
                <input type="number" step="0.1" id="edit_coefficient" name="coefficient" placeholder="ضریب مصرف" class="field">
            </div>
            <div class="flex gap-2 pt-1">
                <button type="submit" class="btn-primary flex-1 py-2.5 text-xs">💾 ذخیره</button>
                <button type="button" onclick="closeEditModal()" class="flex-1 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold py-2.5 rounded-xl cursor-pointer transition-colors">❌ لغو</button>
            </div>
        </form>
    </div>
</div>

<script>
    const SUB_REPO_NAME = "{SUB_REPO_NAME}";
    const DEFAULT_CLEAN_IP = "{DEFAULT_CLEAN_IP}";
    let cachedConfigs = {{}};
    let selectedUserFilter = null;
    let liveTrafficChart = null;
    let chartLabels = [], dsDataSeries = [], usDataSeries = [];

    function switchPanelTab(tabId) {{
        const tabs = ['dashboard','clients','combo_subs','tg_configs','telegram_settings','system_settings','terminal','logs','dpi'];
        tabs.forEach(t => {{
            const sec = document.getElementById('section-tab-' + t);
            const btn = document.getElementById('btn-tab-' + t);
            if (!sec || !btn) return;
            if (t === tabId) {{
                sec.classList.remove('hidden');
                sec.classList.add('slide-up');
                btn.classList.add('tab-active');
                btn.classList.remove('tab-inactive');
            }} else {{
                sec.classList.add('hidden');
                sec.classList.remove('slide-up');
                btn.classList.remove('tab-active');
                btn.classList.add('tab-inactive');
            }}
        }});
    }}

    async function sendLiveTerminalCmd(e) {{
        e.preventDefault();
        const inputEl = document.getElementById('terminal_cmd_input');
        const cmd = inputEl.value.trim();
        if (!cmd) return;
        const consoleEl = document.getElementById('panel_live_terminal_console');
        const prompt = document.getElementById('terminal_dynamic_prompt').innerText;
        consoleEl.innerHTML += `<div class="text-white mt-2 font-bold text-xs">${{prompt}} ${{cmd}}</div>`;
        inputEl.value = "";
        consoleEl.scrollTop = consoleEl.scrollHeight;
        try {{
            let res = await fetch('/api/terminal', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                body: 'command=' + encodeURIComponent(cmd)
            }});
            let data = await res.json();
            let formatted = data.output.replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>');
            consoleEl.innerHTML += `<div class="text-cyan-300/80 bg-black/30 p-2 mt-1 rounded-lg border-l-2 border-indigo-900 whitespace-pre-wrap font-mono select-text text-[10px]">${{formatted}}</div>`;
        }} catch(err) {{
            consoleEl.innerHTML += `<div class="text-rose-400 mt-1 text-xs">❌ خطا در ارتباط با سرور</div>`;
        }}
        consoleEl.scrollTop = consoleEl.scrollHeight;
    }}

    function initSystemCharts() {{
        try {{
            const ctx = document.getElementById('trafficChart').getContext('2d');
            liveTrafficChart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: chartLabels,
                    datasets: [
                        {{ label: '⬇ DL (MB/s)', data: dsDataSeries, borderColor: '#10b981', backgroundColor: 'rgba(16,185,129,0.05)', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 1.5 }},
                        {{ label: '⬆ UL (MB/s)', data: usDataSeries, borderColor: '#6366f1', backgroundColor: 'rgba(99,102,241,0.05)', fill: true, tension: 0.4, pointRadius: 0, borderWidth: 1.5 }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: {{ duration: 200 }},
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{ display: false }},
                        y: {{ beginAtZero: true, grid: {{ color: 'rgba(30,41,59,0.8)' }}, ticks: {{ color: '#475569', font: {{ size: 8 }} }} }}
                    }}
                }}
            }});
        }} catch(e) {{ console.error("Chart:", e); }}
    }}

    function filterUsersList() {{
        let q = (document.getElementById('user_search_input').value || '').toLowerCase().trim();
        ['users_container','tg_users_container'].forEach(id => {{
            let c = document.getElementById(id);
            if (!c) return;
            c.querySelectorAll('div[id^="u_"]').forEach(card => {{
                let name = card.querySelector('.user-name-label')?.innerText.toLowerCase() || '';
                card.style.display = name.includes(q) ? '' : 'none';
            }});
        }});
    }}

    function robustCopy(text, msg) {{
        if (!text) return alert("متنی پیدا نشد!");
        if (navigator.clipboard?.writeText) {{
            navigator.clipboard.writeText(text).then(() => showToast(msg)).catch(() => fallbackCopy(text, msg));
        }} else fallbackCopy(text, msg);
    }}
    function fallbackCopy(text, msg) {{
        const ta = document.createElement("textarea");
        ta.value = text; ta.style.cssText = "position:fixed;opacity:0;";
        document.body.appendChild(ta); ta.focus(); ta.select();
        try {{ document.execCommand('copy'); showToast(msg); }} catch {{ alert(msg); }}
        document.body.removeChild(ta);
    }}
    function showToast(msg) {{
        const t = document.createElement('div');
        t.className = 'fixed bottom-4 left-1/2 -translate-x-1/2 bg-indigo-600 text-white text-xs font-bold px-4 py-2 rounded-2xl shadow-lg z-50 transition-all';
        t.innerText = msg;
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 2000);
    }}

    function copySystemLogs() {{ robustCopy(document.getElementById('sys_terminal').innerText, "📋 لاگ کپی شد!"); }}
    function copyRunnerLogs() {{ robustCopy(document.getElementById('runner_terminal').innerText, "📋 لاگ کپی شد!"); }}
    function copyDpiLogs() {{ robustCopy(document.getElementById('dpi_terminal').innerText, "📋 لاگ کپی شد!"); }}

    async function triggerRunnerTest() {{
        try {{
            let res = await fetch('/api/test_runner');
            let data = await res.json();
            updateRunnerTerminal(data.logs);
            showToast(data.success ? "🚀 رانر متصل شد!" : "❌ اتصال ناموفق");
        }} catch(e) {{ showToast("خطا در ارتباط"); }}
    }}

    function updateRunnerTerminal(logs) {{
        const term = document.getElementById('runner_terminal');
        term.innerHTML = "";
        logs.forEach(l => {{ term.innerHTML += `<div class="border-b border-slate-900/60 pb-0.5 mb-0.5">${{l}}</div>`; }});
        term.scrollTop = term.scrollHeight;
    }}

    function openQrModal(username) {{
        let cfg = cachedConfigs[username];
        if (!cfg) return showToast("⚠️ کانفیگ پیدا نشد");
        document.getElementById('qr_title_user').innerText = username;
        const container = document.getElementById('qrcode_container');
        container.innerHTML = "";
        new QRCode(container, {{ text: cfg, width: 180, height: 180, colorDark:"#020617", colorLight:"#ffffff", correctLevel: QRCode.CorrectLevel.M }});
        document.getElementById('qr_modal_box').style.setProperty('display', 'flex', 'important');
    }}
    function closeQrModal() {{ document.getElementById('qr_modal_box').style.setProperty('display','none','important'); }}

    async function loadLiveStats() {{
        try {{
            let res = await fetch('/api/stats');
            let data = await res.json();

            document.getElementById('online_count').innerText = data.total_online;
            document.getElementById('cpu_val').innerText = data.server_cpu + '%';
            document.getElementById('ram_val').innerText = data.server_ram + '%';
            document.getElementById('total_sys_used').innerText = data.total_sys_used;
            document.getElementById('xray_live_status').innerHTML = data.xray_live
                ? '<span class="text-emerald-400 font-bold">🟢 فعال</span>'
                : '<span class="text-rose-400 font-bold">🔴 متوقف</span>';
            document.getElementById('runner_live_status').innerHTML = data.is_using_runner
                ? `<span class="text-cyan-400 font-bold">🚀 فعال (${{data.runner_speed}})</span>`
                : '<span class="text-amber-400 font-bold">⚠️ تانل معمولی</span>';

            if (data.runner_host) {{
                document.getElementById('terminal_runner_host_display').innerText = data.runner_host;
                let rName = data.runner_host.split('.')[0] || "runner";
                document.getElementById('terminal_dynamic_prompt').innerText = "root@" + rName + ":~#";
            }}

            const termSys = document.getElementById('sys_terminal');
            let scrolled = termSys.scrollHeight - termSys.clientHeight <= termSys.scrollTop + 40;
            termSys.innerHTML = data.sys_logs.map(l => `<div class="border-b border-slate-900/40 pb-0.5 mb-0.5 text-slate-500">${{l}}</div>`).join('');
            if (scrolled) termSys.scrollTop = termSys.scrollHeight;

            const dpiTerm = document.getElementById('dpi_terminal');
            if (data.dpi_logs?.length > 0) {{
                let dpiScrolled = dpiTerm.scrollHeight - dpiTerm.clientHeight <= dpiTerm.scrollTop + 40;
                dpiTerm.innerHTML = data.dpi_logs.map(l => `<div class="border-b border-rose-900/20 pb-0.5 mb-0.5">🛡️ ${{l}}</div>`).join('');
                if (dpiScrolled) dpiTerm.scrollTop = dpiTerm.scrollHeight;
            }}

            if (data.runner_logs) updateRunnerTerminal(data.runner_logs);

            let totDs = 0, totUs = 0;
            data.users.forEach(u => {{
                totDs += u.down_speed_raw || 0;
                totUs += u.up_speed_raw || 0;
                let row = document.getElementById('u_' + u.username);
                if (!row) return;
                row.setAttribute('data-total', u.total_raw);
                row.setAttribute('data-used', u.used_raw);
                row.setAttribute('data-cleanip', u.clean_ip);
                row.setAttribute('data-coef', u.coefficient);
                row.setAttribute('data-real', u.real_traffic);
                row.setAttribute('data-maxips', u.max_ips);
                row.setAttribute('data-customhost', u.custom_host);
                row.setAttribute('data-isproxy', u.is_proxy_type);
                row.setAttribute('data-runnerbalancer', u.use_runner_balancer);
                row.setAttribute('data-optimization', u.optimization);
                row.setAttribute('data-privatetunnel', u.private_tunnel_enabled);
                row.querySelector('.badge').innerText = u.status;
                row.querySelector('.u-used').innerText = u.used;
                row.querySelector('.u-rem').innerText = u.remaining;
                row.querySelector('.u-days').innerText = u.rem_days;
                row.querySelector('.u-dspeed').innerText = u.down_speed;
                row.querySelector('.u-uspeed').innerText = u.up_speed;
                row.querySelector('.p-bar-fill').style.width = u.progress + '%';
                cachedConfigs[u.username] = u.config_raw;
            }});

            document.getElementById('stat_total').innerText = data.users.length;

            // آپدیت چارت
            let ts = new Date().toLocaleTimeString([], {{hour:'2-digit',minute:'2-digit',second:'2-digit'}});
            chartLabels.push(ts);
            dsDataSeries.push((totDs / (1024*1024)).toFixed(3));
            usDataSeries.push((totUs / (1024*1024)).toFixed(3));
            if (chartLabels.length > 20) {{ chartLabels.shift(); dsDataSeries.shift(); usDataSeries.shift(); }}
            if (liveTrafficChart) liveTrafficChart.update('none');

            filterUsersList();

            // مانیتور دامین انتخاب شده
            if (selectedUserFilter) {{
                const u = data.users.find(x => x.username === selectedUserFilter);
                if (u?.destinations?.length > 0) {{
                    document.getElementById('user_sniper_logs').innerHTML =
                        u.destinations.map(d => `<div class="text-indigo-300/90">→ ${{d}}</div>`).join('');
                }}
            }}
        }} catch(e) {{ console.error(e); }}
    }}

    function filterUserSniper(username) {{
        selectedUserFilter = (selectedUserFilter === username) ? null : username;
        document.getElementById('sniper_title').innerText = selectedUserFilter
            ? "🛰️ دامین‌های: " + username
            : "🎯 مانیتور زنده دامین کلاینت";
        if (!selectedUserFilter) document.getElementById('user_sniper_logs').innerHTML = '⚠️ روی کارت کلاینت ضربه بزن.';
    }}

    function copyConfig(user) {{ robustCopy(cachedConfigs[user], '📋 کانفیگ کپی شد!'); }}
    function toggleUnlimitedVolume(cb) {{ document.getElementById('volume_value_input').disabled = cb.checked; }}
    function toggleEditUnlimitedVolume(cb) {{ document.getElementById('edit_volume_value').disabled = cb.checked; }}

    function openEditModalFromRow(username) {{
        let row = document.getElementById('u_' + username);
        if (!row) return;
        openEditModal(
            username,
            row.getAttribute('data-total'), row.getAttribute('data-used'),
            row.getAttribute('data-cleanip'), row.getAttribute('data-coef'),
            row.getAttribute('data-maxips'), row.getAttribute('data-customhost'),
            row.getAttribute('data-real') === 'true',
            row.getAttribute('data-runnerbalancer') === 'true',
            row.getAttribute('data-optimization') === 'true',
            row.getAttribute('data-privatetunnel') === 'true'
        );
    }}
    function openEditModal(username, totalBytes, usedBytes, cleanIp, coef, maxIps, customHost, isReal, runnerBalancer, optimization, privateTunnel) {{
        document.getElementById('edit_username').value = username;
        document.getElementById('edit_title_user').innerText = username;
        document.getElementById('edit_clean_ip').value = cleanIp;
        document.getElementById('edit_coefficient').value = coef;
        document.getElementById('edit_max_ips').value = maxIps;
        document.getElementById('edit_custom_host').value = customHost || "";
        document.getElementById('edit_use_runner_balancer').checked = runnerBalancer;
        document.getElementById('edit_optimization').checked = optimization;
        document.getElementById('edit_private_tunnel_enabled').checked = privateTunnel;
        let isUnl = parseInt(totalBytes) === 0;
        document.getElementById('edit_unlimited_volume').checked = isUnl;
        document.getElementById('edit_volume_value').disabled = isUnl;
        document.getElementById('edit_volume_value').value = isUnl ? "" : (parseInt(totalBytes) / (1024**3)).toFixed(2);
        document.getElementById('edit_used_value').value = (parseInt(usedBytes) / (1024**3)).toFixed(2);
        document.getElementById('edit_real_traffic').checked = isReal;
        document.getElementById('edit_modal_box').style.setProperty('display', 'flex', 'important');
    }}
    function closeEditModal() {{ document.getElementById('edit_modal_box').style.setProperty('display','none','important'); }}
    function copyFixedSubscription(user) {{ robustCopy("https://raw.githubusercontent.com/" + SUB_REPO_NAME + "/main/" + user, "🔗 لینک ساب کپی شد!"); }}
    function copyComboSubLink(comboName) {{ robustCopy("https://raw.githubusercontent.com/" + SUB_REPO_NAME + "/main/combo_" + comboName, "🔗 لینک ساب ترکیبی کپی شد!"); }}

    initSystemCharts();
    setInterval(loadLiveStats, 2500);
    loadLiveStats();
</script>
</body>
</html>"""

            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()


# ─────────────────────────────────────────────
# FIX: xray_live_log_sniffer — محاسبه واقعی حجم
# ─────────────────────────────────────────────
def xray_live_log_sniffer():
    """
    تحلیل لاگ xray.
    
    برای real_traffic=True:
      فقط از uplink/downlink واقعی لاگ استفاده میکنه.
      اگه این مقدار در لاگ نبود، هیچ چیز به used_bytes اضافه نمیشه.
    
    برای real_traffic=False:
      مثل قبل از ضریب و مقادیر تخمینی استفاده میکنه.
    """
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

            PANEL_DATABASE[user_name]["last_active_time"] = time.time()
            if PANEL_DATABASE[user_name].get("status") != "IP_LIMIT_EXCEEDED":
                PANEL_DATABASE[user_name]["status"] = "ONLINE"

            # IP زنده
            ip_match = IP_REGEX.search(clean_line)
            if ip_match:
                client_ip = ip_match.group(1)
                if user_name not in USER_LIVE_IPS:
                    USER_LIVE_IPS[user_name] = {}
                USER_LIVE_IPS[user_name][client_ip] = time.time()

            # دامین
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

            # ── FIX: پارس دقیق بایت از لاگ ──
            traffic_match = REAL_TRAFFIC_REGEX.search(clean_line)

            if is_real:
                # حالت تحلیل واقعی: فقط از مقادیر واقعی لاگ استفاده کن
                if traffic_match:
                    # uplink + downlink یا size
                    uplink = int(traffic_match.group(1) or 0)
                    downlink = int(traffic_match.group(2) or 0)
                    size_val = int(traffic_match.group(3) or 0)
                    uploaded_val = int(traffic_match.group(4) or 0)

                    if uplink > 0 or downlink > 0:
                        # xray لاگ نهایی یه session رو با uplink/downlink میده
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
                # اگه traffic_match نبود، هیچی اضافه نمیشه (این درست‌ترین رفتاره)
                # فقط speed رو صفر نگه دار تا بعد از timeout پاک بشه

            else:
                # حالت تخمینی (real_traffic=False)
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
                        # تخمین برای رویدادهای بدون اندازه
                        fake_bytes = secrets.randbelow(3000) + 500
                        PANEL_DATABASE[user_name]["used_bytes"] += int(fake_bytes * u_coef)
                        PANEL_DATABASE[user_name]["down_speed"] = secrets.randbelow(800000) + 200000
                        PANEL_DATABASE[user_name]["up_speed"] = secrets.randbelow(20000) + 30000
                else:
                    # لاگ بدون عدد (مثل "accepted" یا "connected")
                    fake_bytes = secrets.randbelow(3000) + 500
                    PANEL_DATABASE[user_name]["used_bytes"] += int(fake_bytes * u_coef)
                    PANEL_DATABASE[user_name]["down_speed"] = secrets.randbelow(800000) + 200000
                    PANEL_DATABASE[user_name]["up_speed"] = secrets.randbelow(20000) + 30000

            save_database()


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


def channel_live_stream_worker(bot_instance):
    try:
        init_text = (
            f"📡 *استریم زنده مدیریت سیستم kill_pv2*\n\n"
            f"🟢 سرویس راه‌اندازی شد\n"
            f"⏱️ شروع: `{time.strftime('%Y-%m-%d %H:%M:%S')}`\n\n"
            f"_در حال انتظار رویدادها..._"
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
                current_events = list(CHANNEL_STREAM_STATE["events"][-12:])
                if current_events == last_rendered_events:
                    continue
                cpu_v, ram_v = get_server_resources()
                total_users = len(PANEL_DATABASE)
                active_users = sum(1 for v in PANEL_DATABASE.values() if v.get("active", True))
                online_users = sum(1 for k, v in PANEL_DATABASE.items() if len(USER_LIVE_IPS.get(k, {})) > 0 and v.get("active", True))
                events_block = "\n".join(current_events) if current_events else "_رویدادی ثبت نشده_"
                stream_text = (
                    f"📡 *استریم زنده kill_pv2*\n\n"
                    f"⏱️ `{time.strftime('%H:%M:%S')}`\n"
                    f"👥 `{online_users}` آنلاین | `{active_users}` فعال | `{total_users}` کل\n"
                    f"🖥️ CPU `{cpu_v}%` | RAM `{ram_v}%`\n"
                    f"🛡️ Xray: {'🟢 فعال' if is_xray_core_running() else '🔴 متوقف'}\n\n"
                    f"📋 *رویدادهای اخیر:*\n{events_block}"
                )
                try:
                    bot_instance.edit_message_text(stream_text, TELEGRAM_CHANNEL_ID, CHANNEL_STREAM_STATE["msg_id"], parse_mode="Markdown")
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
                    f"👑 *سلام داداش!*\n\n"
                    f"📊 *وضعیت چالش:*\n"
                    f"👥 `{g_config['claimed_count']}` از `{g_config['max_claims']}`\n"
                    f"💾 `{g_config.get('volume_value', 0)} {g_config.get('volume_unit', 'GB')}`\n"
                    f"⚙️ `{g_config.get('status', 'inactive')}`\n\n"
                    f"🛠️ کانفیگ‌های رایگان: `{total_free_cnt}`"
                )
                markup = ReplyKeyboardMarkup(resize_keyboard=True)
                markup.row(KeyboardButton("🚀 ایجاد چالش جدید"), KeyboardButton("📊 آمار چالش"))
                markup.row(KeyboardButton("🛠️ مدیریت وضعیت چالش"))
                # 🆕 دکمه ساخت تونل جدید برای کاربر
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
                            bot.send_message(TELEGRAM_CHANNEL_ID, "🏁 ظرفیت تموم شد!", reply_to_message_id=g_config["channel_msg_id"])
                        except Exception:
                            pass

                save_database()
                save_giveaway_config(g_config)
                sync_xray_core()
                push_subs_to_github()
                push_channel_event(f"🎁 کلیم شد: {new_username}")

                t_host = runner_host
                vless_link = f"vless://{PANEL_DATABASE[new_username]['uuid']}@{DEFAULT_CLEAN_IP}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{new_username}_⚡Opt"
                sub_link = f"https://raw.githubusercontent.com/{SUB_REPO_NAME}/main/{new_username}"
                vol_display = f"{g_config.get('volume_value', 0)} {g_config.get('volume_unit', 'GB')}"
                success_text = (
                    f"🎉 *تبریک!*\n\n"
                    f"👤 `{new_username}`\n"
                    f"💾 `{vol_display}`\n\n"
                    f"📋 *کانفیگ:*\n`{vless_link}`\n\n"
                    f"🔗 *ساب:*\n`{sub_link}`"
                )
                user_kb = ReplyKeyboardMarkup(resize_keyboard=True)
                user_kb.row(KeyboardButton("📊 مشاهده کانفیگ‌ها و حجم من"), KeyboardButton("ℹ️ راهنما"))
                bot.send_message(message.chat.id, success_text, parse_mode="Markdown", reply_markup=user_kb)
                try:
                    qr_buf = generate_qr_png_bytes(vless_link)
                    if qr_buf:
                        bot.send_photo(message.chat.id, qr_buf, caption=f"📱 QR `{new_username}`", parse_mode="Markdown")
                except Exception:
                    pass
                try:
                    bot.send_message(TELEGRAM_ADMIN_ID, f"🔔 `{new_username}` دریافت شد.")
                except Exception:
                    pass
            else:
                user_kb = ReplyKeyboardMarkup(resize_keyboard=True)
                user_kb.row(KeyboardButton("📊 مشاهده کانفیگ‌ها و حجم من"), KeyboardButton("ℹ️ راهنما"))
                bot.send_message(message.chat.id, "👋 سلام! برای دریافت کانفیگ از لینک چالش استفاده کن.", reply_markup=user_kb)

        @bot.message_handler(func=lambda msg: msg.text == "📊 مشاهده کانفیگ‌ها و حجم من")
        def handle_user_stats(message):
            chat_id_str = str(message.chat.id)
            configs_found = [(k, v) for k, v in PANEL_DATABASE.items() if str(v.get("tg_user_id", "")) == chat_id_str]
            if not configs_found:
                bot.send_message(message.chat.id, "⚠️ کانفیگی برای شما یافت نشد.")
                return
            now = int(time.time())
            resp = "📊 *کانفیگ‌های شما:*\n\n"
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
                vless_link = f"vless://{u_data.get('uuid', '')}@{DEFAULT_CLEAN_IP}:443?path=%2Fkillpv2&security=tls&encryption=none&insecure=0&type=ws&allowInsecure=0&host={t_host}&sni={t_host}#{u_name}{suffix}"
                sub_link = f"https://raw.githubusercontent.com/{SUB_REPO_NAME}/main/{u_name}"
                resp += (
                    f"{'🟢' if u_data.get('active', True) else '🔴'} `{u_name}`\n"
                    f"💾 کل: `{format_bytes_display(total_l) if total_l > 0 else 'نامحدود'}`\n"
                    f"📊 مصرف: `{format_bytes_display(used)}`\n"
                    f"💾 باقی: `{format_bytes_display(rem) if total_l > 0 else 'نامحدود'}`\n"
                    f"⏳ `{rem_d} روز و {rem_h} ساعت`\n\n"
                    f"📋 `{vless_link}`\n🔗 `{sub_link}`\n─────────────\n"
                )
            bot.send_message(message.chat.id, resp, parse_mode="Markdown")

        @bot.message_handler(func=lambda msg: msg.text == "ℹ️ راهنما")
        def handle_help(message):
            bot.send_message(message.chat.id,
                "ℹ️ *راهنما:*\n▪️ اندروید: `v2rayNG` / `NekoBox`\n▪️ آیفون: `v2box` / `FoXray`\n▪️ ویندوز: `v2rayN`",
                parse_mode="Markdown")

        # ─────────────────────────────────────────────
        # 🆕 دکمه ساخت تونل اختصاصی در ربات برای ادمین
        # ─────────────────────────────────────────────
        @bot.message_handler(func=lambda msg: str(msg.chat.id) == str(TELEGRAM_ADMIN_ID) and msg.text == "🔒 ساخت تونل اختصاصی برای کاربر")
        def handle_admin_build_tunnel(message):
            """
            ادمین میتونه از ربات برای یه کاربر خاص تونل جدید بسازه.
            """
            # لیست کاربرهای فعال رو بفرست
            active_users = [k for k, v in PANEL_DATABASE.items() if v.get("active", True) and not v.get("is_proxy_type", False)]
            if not active_users:
                bot.send_message(message.chat.id, "❌ هیچ کاربر فعالی وجود ندارد.")
                return

            # ساخت دکمه‌های inline برای انتخاب کاربر
            markup = InlineKeyboardMarkup(row_width=2)
            buttons = [InlineKeyboardButton(u, callback_data=f"build_tunnel_{u}") for u in active_users[:20]]
            markup.add(*buttons)
            bot.send_message(
                message.chat.id,
                "👤 *برای کدام کاربر تونل اختصاصی بسازم؟*\n\n"
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
                bot.send_message(message.chat.id,
                    f"📊 *آمار:*\n👥 `{g_config['claimed_count']}/{g_config['max_claims']}`\n"
                    f"💾 `{g_config.get('volume_value', 0)} {g_config.get('volume_unit', 'GB')}`\n"
                    f"⚙️ `{g_config.get('status', 'inactive')}`",
                    parse_mode="Markdown")
            elif message.text == "🛠️ مدیریت وضعیت چالش":
                g_config = load_giveaway_config()
                status_curr = g_config.get("status", "inactive")
                mk = InlineKeyboardMarkup()
                if status_curr == "active":
                    mk.add(InlineKeyboardButton("🛑 لغو", callback_data="tg_camp_cancel"))
                elif status_curr == "cancelled":
                    mk.add(InlineKeyboardButton("🟢 فعال‌سازی", callback_data="tg_camp_activate"))
                mk.add(InlineKeyboardButton("🗑️ حذف کامل", callback_data="tg_camp_delete"))
                bot.send_message(message.chat.id, f"⚙️ وضعیت: *{status_curr}*", parse_mode="Markdown", reply_markup=mk)

        def process_capacity_step(message):
            try:
                capacity = int(message.text.strip())
                msg_s = bot.send_message(message.chat.id, "💾 مقدار حجم:")
                bot.register_next_step_handler(msg_s, lambda m: process_volume_value_step(m, capacity))
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

            # ─── 🆕 ساخت تونل اختصاصی از ربات ───
            if call.data.startswith("build_tunnel_"):
                target_user = call.data.replace("build_tunnel_", "", 1)
                if target_user not in PANEL_DATABASE:
                    bot.answer_callback_query(call.id, "❌ کاربر یافت نشد!")
                    return

                bot.answer_callback_query(call.id, "🔄 در حال ساخت تونل...")
                bot.edit_message_text(
                    f"🔄 در حال ساخت تونل اختصاصی برای `{target_user}`...\nلطفاً صبر کن (~۳۵ ثانیه)",
                    call.message.chat.id, call.message.message_id, parse_mode="Markdown"
                )

                # ساخت تونل رو توی thread جدا اجرا کن تا بات freeze نشه
                def do_build():
                    try:
                        # اگه قبلاً تونل نداشته، private_tunnel_enabled رو هم فعال کن
                        PANEL_DATABASE[target_user]["private_tunnel_enabled"] = True
                        new_host = spawn_private_tunnel_for_user(target_user)
                        if new_host:
                            PANEL_DATABASE[target_user]["private_tunnel_host"] = new_host
                            save_database()
                            sync_xray_core()
                            push_subs_to_github()
                            push_channel_event(f"🔒 تونل اختصاصی از ربات ساخته شد: {target_user} → {new_host}")
                            result_msg = (
                                f"✅ *تونل اختصاصی ساخته شد!*\n\n"
                                f"👤 کاربر: `{target_user}`\n"
                                f"🌐 هاست: `{new_host}`\n\n"
                                f"ساب لینک آپدیت شد و از این تونل استفاده میکنه."
                            )
                        else:
                            result_msg = f"❌ ساخت تونل برای `{target_user}` ناموفق بود.\nممکنه cloudflared در دسترس نباشه."

                        bot.edit_message_text(result_msg, call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                    except Exception as e:
                        try:
                            bot.edit_message_text(f"❌ خطا: {str(e)}", call.message.chat.id, call.message.message_id)
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
                    "max_claims": capacity, "volume_value": volume_val, "volume_unit": unit,
                    "volume_gb": volume_gb, "claimed_count": 0, "claimed_users": [],
                    "status": "active", "channel_msg_id": None
                }
                save_giveaway_config(g_config)
                bot_info = bot.get_me()
                share_url = f"https://t.me/{bot_info.username}?start=claim"
                mk = InlineKeyboardMarkup()
                mk.add(InlineKeyboardButton("🎁 دریافت رایگان", url=share_url))
                ch_text = f"🚀 *چالش جدید!*\n👥 ظرفیت: `{capacity}`\n💾 حجم: `{volume_val} {unit}`"
                sent_ch = bot.send_message(TELEGRAM_CHANNEL_ID, ch_text, reply_markup=mk, parse_mode="Markdown")
                g_config["channel_msg_id"] = sent_ch.message_id
                save_giveaway_config(g_config)
                push_channel_event(f"🚀 چالش جدید: {capacity}، {volume_val} {unit}")
                bot.answer_callback_query(call.id, "✅ ایجاد شد!")
                bot.send_message(call.message.chat.id, "✅ چالش در کانال ارسال شد!")
            elif call.data == "tg_camp_cancel":
                g_config["status"] = "cancelled"
                save_giveaway_config(g_config)
                bot.answer_callback_query(call.id, "لغو شد.")
                bot.edit_message_text("🛑 *لغو شد*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                push_channel_event("🛑 چالش لغو شد")
            elif call.data == "tg_camp_activate":
                g_config["status"] = "active"
                save_giveaway_config(g_config)
                bot.answer_callback_query(call.id, "فعال شد.")
                bot.edit_message_text("🟢 *فعال شد*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
                push_channel_event("🟢 چالش فعال شد")
            elif call.data == "tg_camp_delete":
                g_config = {"max_claims": 0, "volume_value": 0.0, "volume_unit": "GB", "volume_gb": 0.0, "claimed_count": 0, "claimed_users": [], "status": "inactive", "channel_msg_id": None}
                save_giveaway_config(g_config)
                bot.answer_callback_query(call.id, "حذف شد.")
                bot.edit_message_text("🗑️ حذف شد.", call.message.chat.id, call.message.message_id)
                push_channel_event("🗑️ چالش حذف شد")

        threading.Thread(target=lambda: bot.infinity_polling(timeout=20, long_polling_timeout=10), daemon=True).start()
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

# FIX: اول هاست‌های قدیمی پاک میشن، بعد تونل جدید ساخته میشه
bootstrap_private_tunnels_on_startup()

# حالا با هاست‌های جدید پوش کن
push_subs_to_github()
init_telegram_bot_service()

threading.Thread(target=lambda: HTTPServer(('127.0.0.1', 8086), SanaeiMobileXuiServer).serve_forever(), daemon=True).start()
threading.Thread(target=xray_live_log_sniffer, daemon=True).start()
threading.Thread(target=speed_and_ip_cleaner, daemon=True).start()

push_channel_event("🚀 سرویس kill_pv2 بالا اومد")

total_duration = 19800
elapsed = 0
last_github_update_time = time.time()

while elapsed < total_duration:
    time.sleep(5)
    elapsed += 5
    check_expiration_and_limits()
    if time.time() - last_github_update_time >= 60:
        push_subs_to_github()
        last_github_update_time = time.time()
