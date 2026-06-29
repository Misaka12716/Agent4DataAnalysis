import os

def is_sandbox_enabled() -> bool:
    return os.getenv("CUBE_SANDBOX_ENABLED", "0") == "1"

CUBE_SANDBOX_ENABLED = is_sandbox_enabled()
E2B_API_URL = os.getenv("E2B_API_URL", "http://127.0.0.1:3000")
E2B_API_KEY = os.getenv("E2B_API_KEY", "e2b_000000")
CUBE_TEMPLATE_ID = os.getenv("CUBE_TEMPLATE_ID", "tpl-78c1861fc2b54381947d33e2")
SANDBOX_WORKDIR = os.getenv("SANDBOX_WORKDIR", "/home/user")
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "600"))

META_FILENAME = ".cube_sandbox_meta.json"
SANDBOX_LIST_DEPTH = int(os.getenv("SANDBOX_LIST_DEPTH", "20"))
