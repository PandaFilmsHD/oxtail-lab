"""Phone alerts.

1st choice: web push straight to the installed Oxtail app (tap opens the app). Needs the PUSH_SUB secret
            (the code from Settings -> Turn on alerts). The signing key is derived from the NTFY_TOPIC secret,
            so it only ever exists inside GitHub Actions; only its public half is published (docs/push_key.txt).
Fallback:   ntfy app, if NTFY_TOPIC is set.

python3 scripts/notify.py test   -> sends a sample alert
"""
import os, sys, json, pathlib, requests

ROOT = pathlib.Path(__file__).resolve().parents[1]
SITE = "https://pandafilmshd.github.io/oxtail-lab/"


def _vapid():
    """Signing key derived from a secret that never leaves GitHub (NTFY_TOPIC)."""
    import hashlib, base64
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    seed = os.environ.get("NTFY_TOPIC", "").strip()
    if not seed:
        return None, None
    n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    d = int.from_bytes(hashlib.sha256(("oxtail-vapid|" + seed).encode()).digest(), "big") % (n - 1) + 1
    key = ec.derive_private_key(d, ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    pub = base64.urlsafe_b64encode(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).decode().rstrip("=")
    return pem, pub


def publish_public_key():
    _, pub = _vapid()
    if pub:
        p = ROOT / "docs" / "push_key.txt"
        if not p.exists() or p.read_text().strip() != pub:
            p.write_text(pub)
            print("notify: published the public push key")


def _webpush(title, body, url, tag):
    raw = os.environ.get("PUSH_SUB", "").strip()
    if not raw:
        return None
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid01
    pem, _ = _vapid()
    if not pem:
        return None
    vk = Vapid01.from_pem(pem.encode())
    subs = json.loads(raw)
    subs = subs if isinstance(subs, list) else [subs]
    ok = False
    for sub in subs:
        try:
            webpush(subscription_info=sub, data=json.dumps({"title": title, "body": body, "url": url, "tag": tag}),
                    vapid_private_key=vk, vapid_claims={"sub": "mailto:oxtail-bot@users.noreply.github.com"}, ttl=3600)
            ok = True
        except WebPushException as e:
            print("notify: web push failed", getattr(e.response, "status_code", ""), str(e)[:120])
    return ok


def send(title, body, tags=None, priority=3, click=None, tag=None):
    url = click or SITE
    r = _webpush(title, body, url, tag)
    if r:
        print(f"notify (app): {title}")
        return True
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("notify: no PUSH_SUB or NTFY_TOPIC set, skipping")
        return False
    x = requests.post("https://ntfy.sh/", json={"topic": topic, "title": title, "message": body, "tags": tags or [], "priority": priority, "click": url}, timeout=20)
    print(f"notify (ntfy): {title} -> {x.status_code}")
    return x.ok


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "publish":
    publish_public_key()
if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "test":
    publish_public_key()
    send("🔔 Oxtail alerts are on (test)", "This came straight from GitHub to the Oxtail app. Tap to open it. Real alerts: injuries, lineup problems, in-game updates.", ["bell"], 4)
