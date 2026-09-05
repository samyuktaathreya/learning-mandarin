from slowapi import Limiter
from slowapi.util import get_remote_address

def client_ip(request):
    return (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or get_remote_address(request)
    )

limiter = Limiter(key_func=client_ip, default_limits=["30/minute"])