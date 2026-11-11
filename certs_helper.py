import os
import ssl
import urllib.request
from pathlib import Path

ROOT_CA = "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt"
SUB_CA = "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt"
CERT_DIR = Path(os.getenv("CERT_DIR", "/app/certs/russian-trusted"))


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "bothost-bot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if b"BEGIN CERTIFICATE" not in data:
        raise RuntimeError(f"Не сертификат: {url}")
    return data if data.endswith(b"\n") else data + b"\n"


def _system_bundle() -> bytes:
    """Ищем системный CA bundle в типичных местах Linux-контейнера."""
    for path in (
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/ssl/cert.pem",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ):
        p = Path(path)
        if p.is_file():
            data = p.read_bytes()
            return data if data.endswith(b"\n") else data + b"\n"
    try:
        import certifi
        data = Path(certifi.where()).read_bytes()
        return data if data.endswith(b"\n") else data + b"\n"
    except Exception:
        return b""


def ensure_mincifry_certs() -> Path:
    """
    Скачивает CA Минцифры, собирает combined bundle (системные + российские)
    и выставляет переменные окружения, которые подхватывают OpenSSL, requests,
    httpx и Node.js.
    """
    CERT_DIR.mkdir(parents=True, exist_ok=True)

    russian = _download(ROOT_CA) + _download(SUB_CA)
    russian_path = CERT_DIR / "russian_trusted_ca_bundle.crt"
    russian_path.write_bytes(russian)

    combined = _system_bundle() + russian
    combined_path = CERT_DIR / "ca-bundle.crt"
    combined_path.write_bytes(combined)

    os.environ["SSL_CERT_FILE"] = str(combined_path)
    os.environ["REQUESTS_CA_BUNDLE"] = str(combined_path)
    os.environ["CURL_CA_BUNDLE"] = str(combined_path)
    os.environ["NODE_EXTRA_CA_CERTS"] = str(russian_path)

    # Прогрев, чтобы процесс гарантированно подхватил SSL_CERT_FILE
    ssl.create_default_context()
    return combined_path
