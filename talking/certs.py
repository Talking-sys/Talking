"""Self-signed TLS for phone microphones on a LAN (browsers require HTTPS or localhost)."""
from __future__ import annotations

import datetime
import os
import socket
from pathlib import Path


def local_names() -> list[str]:
    names = {"localhost"}
    try:
        names.add(socket.gethostname())
    except Exception:
        pass
    try:
        names.add(socket.getfqdn())
    except Exception:
        pass
    return sorted(n for n in names if n)


def local_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
    except Exception:
        pass
    return sorted(ips)


def self_signed(data_dir: Path) -> tuple[str, str]:
    from ipaddress import ip_address

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    data_dir.mkdir(parents=True, exist_ok=True)
    cert, key = data_dir / "selfsigned.crt", data_dir / "selfsigned.key"
    if cert.exists() and key.exists():
        return str(cert), str(key)

    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Talking LAN")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san: list[x509.GeneralName] = [x509.DNSName(n) for n in local_names()]
    for ip in local_ips():
        try:
            san.append(x509.IPAddress(ip_address(ip)))
        except ValueError:
            continue
    c = (x509.CertificateBuilder()
         .subject_name(name).issuer_name(name)
         .public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(now)
         .not_valid_after(now + datetime.timedelta(days=365))
         .add_extension(x509.SubjectAlternativeName(san), False)
         .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    os.chmod(key, 0o600)
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return str(cert), str(key)
