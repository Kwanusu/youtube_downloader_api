"""Optional e-mail alerts when a recording fails or the service needs attention."""
import smtplib
from email.message import EmailMessage

import config


def alert(subject, body):
    print(f"ALERT: {subject}\n{body}")
    if not (config.SMTP_HOST and config.ALERT_EMAIL_TO):
        return
    msg = EmailMessage()
    msg["Subject"] = f"[Class uploader] {subject}"
    msg["From"] = config.SMTP_USER or config.ALERT_EMAIL_TO
    msg["To"] = config.ALERT_EMAIL_TO
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        print(f"Could not send alert e-mail: {e}")
