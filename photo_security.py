"""Short-lived image links keep stored profile photos out of the public file server."""
import hashlib
import hmac
import time
from urllib.parse import urlencode

PHOTO_LINK_SECONDS = 3600


def _signature(filename, expires):
    from security import SECRET_KEY
    return hmac.new(SECRET_KEY.encode(), f"profile-photo:{filename}:{expires}".encode(), hashlib.sha256).hexdigest()


def signed_photo_url(value):
    if not value or not value.startswith("/profile-photos/"):
        return value
    filename = value.split("?", 1)[0].removeprefix("/profile-photos/")
    expires = int(time.time()) + PHOTO_LINK_SECONDS
    return f"/profile-photos/{filename}?{urlencode({'expires': expires, 'signature': _signature(filename, expires)})}"


def valid_photo_link(filename, expires, signature):
    now = int(time.time())
    return (now <= expires <= now + PHOTO_LINK_SECONDS
            and hmac.compare_digest(_signature(filename, expires), signature))
