"""Serve SunShop to the lab (local network and Tailscale) with waitress (production mode).

Usage:  .venv\\Scripts\\python serve.py [port]
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080


def lan_ip():
    # No packets are sent; this just asks the OS which interface it would route through.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]


def tailscale_ip():
    # This PC's fixed 100.x.y.z Tailscale address, or None if Tailscale isn't installed/running.
    # (MagicDNS names don't resolve on our tailnet, so lab members use the IP.)
    exe = shutil.which("tailscale") or r"C:\Program Files\Tailscale\tailscale.exe"
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, encoding="utf-8", timeout=10).stdout
        return json.loads(out)["Self"]["TailscaleIPs"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        return None


def secret_key():
    # Generated once and kept out of git, so sessions survive restarts.
    path = BASE_DIR / ".secret_key"
    if not path.exists():
        path.write_text(secrets.token_urlsafe(50))
    return path.read_text().strip()


ip = lan_ip()
hostname = socket.gethostname()
fqdn = socket.getfqdn()
ts_ip = tailscale_ip()
hosts = [ip, hostname, fqdn, "localhost", "127.0.0.1"]
if ts_ip:
    hosts.append(ts_ip)
os.environ.setdefault("SUNSHOP_DEBUG", "0")
os.environ.setdefault("SUNSHOP_SECRET_KEY", secret_key())
os.environ.setdefault("SUNSHOP_ALLOWED_HOSTS", ",".join(hosts))
# Links in notification emails point here.
os.environ.setdefault("SUNSHOP_SITE_URL", f"http://{ts_ip or ip}:{PORT}")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "SunShop.settings")

import django
from django.core.management import call_command

django.setup()
call_command("collectstatic", interactive=False, verbosity=0)

from waitress import serve
from SunShop.wsgi import application

print(f"SunShop running at {os.environ['SUNSHOP_SITE_URL']}  (Ctrl+C to stop)")
serve(application, listen=f"*:{PORT}", threads=8)
