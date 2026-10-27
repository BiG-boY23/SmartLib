"""Public distribution metadata; release links are configured only after publishing."""
import os
import re
from urllib.parse import urlsplit

from production import public_https_url


def app_releases():
    android = os.getenv("ANDROID_APK_URL", "").strip()
    ios = os.getenv("IOS_TESTFLIGHT_URL", "").strip()
    android = android if public_https_url(android) else None
    try:
        parsed = urlsplit(ios)
    except ValueError:
        parsed = urlsplit("")
        ios = ""
    ios = ios if (public_https_url(ios) and parsed.hostname == "testflight.apple.com"
                  and re.fullmatch(r"/join/[A-Za-z0-9]+/?", parsed.path)) else None
    return {
        "version": os.getenv("MOBILE_APP_VERSION", "1.0.0")[:40],
        "android": {"available": bool(android), "url": android},
        "ios": {"available": bool(ios), "url": ios},
    }
