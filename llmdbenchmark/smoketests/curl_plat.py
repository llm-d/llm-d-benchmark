import os
import platform

def get_curl_image():
    arch = os.getenv("TARGET_ARCH", "").lower()
    
    if not arch:
        arch = platform.machine()

    arch = arch.lower()

    # Normalize common arch names
    if arch == "s390x":
        return "lucashalbert/curl"
    else:
        return "quay.io/curl/curl"
