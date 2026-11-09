"""Pluggable mail delivery for passwordless email sign-in.

SMTP is the production path. The console sink is for local development and
tests — it never prints the destination address, code, or magic link, and is
refused when APP_ENV=production.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

log = logging.getLogger("talking.auth")


@dataclass
class MailSendResult:
    ok: bool
    provider: str
    error: str | None = None


class MailProvider(Protocol):
    name: str

    async def send(
        self, *, to_email: str, subject: str, text: str, html: str | None = None,
    ) -> MailSendResult: ...


class ConsoleMailSink:
    """Dev/test sink. Does not deliver or print destination, codes, or links."""

    name = "console"

    async def send(
        self, *, to_email: str, subject: str, text: str, html: str | None = None,
    ) -> MailSendResult:
        log.info("dev email verification started")
        return MailSendResult(ok=True, provider=self.name)


class SmtpMailProvider:
    name = "smtp"

    def __init__(
        self,
        host: str,
        port: int,
        from_addr: str,
        *,
        username: str = "",
        password: str = "",
        use_tls: bool = True,
        use_ssl: bool = False,
        timeout: float = 10.0,
    ):
        self.host = host
        self.port = port
        self.from_addr = from_addr
        self.username = username
        self.password = password
        self.use_tls = use_tls
        self.use_ssl = use_ssl
        self.timeout = timeout

    def _deliver(self, to_email: str, subject: str, text: str, html: str | None) -> None:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        context = ssl.create_default_context()
        if self.use_ssl:
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, timeout=self.timeout, context=context,
            )
        else:
            smtp = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
        try:
            smtp.ehlo()
            if self.use_tls and not self.use_ssl:
                smtp.starttls(context=context)
                smtp.ehlo()
            if self.username:
                smtp.login(self.username, self.password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass

    async def send(
        self, *, to_email: str, subject: str, text: str, html: str | None = None,
    ) -> MailSendResult:
        try:
            await asyncio.to_thread(self._deliver, to_email, subject, text, html)
            return MailSendResult(ok=True, provider=self.name)
        except Exception:
            log.warning("smtp send failed")
            return MailSendResult(ok=False, provider=self.name, error="provider_error")


def mail_provider_name(settings) -> str:
    return (getattr(settings, "mail_provider", None) or "").strip().lower()


def smtp_credentials(settings) -> tuple[str, int, str, str, str, bool, bool]:
    host = (getattr(settings, "smtp_host", None) or "").strip()
    try:
        port = int(getattr(settings, "smtp_port", 587) or 587)
    except (TypeError, ValueError):
        port = 587
    from_addr = (getattr(settings, "smtp_from", None) or "").strip()
    username = (getattr(settings, "smtp_username", None) or "").strip()
    password = getattr(settings, "smtp_password", None) or ""
    use_ssl = bool(getattr(settings, "smtp_ssl", False))
    use_tls = bool(getattr(settings, "smtp_starttls", True))
    if use_ssl:
        use_tls = False
    return host, port, from_addr, username, password, use_tls, use_ssl


def smtp_configured(settings) -> bool:
    host, _port, from_addr, *_rest = smtp_credentials(settings)
    return bool(host and from_addr)


def build_mail_provider(settings) -> MailProvider:
    name = mail_provider_name(settings) or "console"
    production = bool(getattr(settings, "production", False))
    if name == "smtp":
        if not smtp_configured(settings):
            raise RuntimeError(
                "SMTP_HOST and SMTP_FROM are required when MAIL_PROVIDER=smtp"
            )
        host, port, from_addr, username, password, use_tls, use_ssl = smtp_credentials(settings)
        return SmtpMailProvider(
            host, port, from_addr,
            username=username, password=password, use_tls=use_tls, use_ssl=use_ssl,
        )
    if production:
        raise RuntimeError(
            "MAIL_PROVIDER=console is refused when APP_ENV=production; "
            "set MAIL_PROVIDER=smtp with SMTP_HOST and SMTP_FROM"
        )
    return ConsoleMailSink()
