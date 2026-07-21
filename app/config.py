"""
PLM Lite V1.0 — Configuration
All settings loaded from .env via python-dotenv.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Environment ──────────────────────────────────────────────────────────────
# "development" | "production". Controls whether insecure defaults are tolerated.
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION: bool = ENVIRONMENT in ("production", "prod")

# ── Auth ─────────────────────────────────────────────────────────────────────
AUTH_MODE: str = os.getenv("AUTH_MODE", "local").lower()  # "google" | "local" | "windows"
_DEFAULT_SECRET_KEY = "change-me-in-production-use-openssl-rand-hex-32"
SECRET_KEY: str = os.getenv("SECRET_KEY", _DEFAULT_SECRET_KEY)
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_HOURS: int = int(os.getenv("JWT_EXPIRE_HOURS", "8"))

# Minimum length enforced when users set or change a password.
PASSWORD_MIN_LENGTH: int = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))

# Google OAuth (only needed when AUTH_MODE=google)
GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8080")

# ── Storage ──────────────────────────────────────────────────────────────────
FILES_ROOT: Path = Path(os.getenv("FILES_ROOT", "/srv/plm/files"))
DB_PATH: Path = Path(os.getenv("DB_PATH", "/srv/plm/plm.db"))

# ── File versioning ──────────────────────────────────────────────────────────
MAX_FILE_VERSIONS: int = int(os.getenv("MAX_FILE_VERSIONS", "3"))

CAD_EXTENSIONS: set[str] = set(
    ext.strip().lower()
    for ext in os.getenv(
        "CAD_EXTENSIONS",
        ".prt,.asm,.drw,.stl,.3mf,.obj,.step,.stp,.sldprt,.sldasm,.ipt,.iam",
    ).split(",")
    if ext.strip()
)

# ── Network open-in-place ────────────────────────────────────────────────────
# UNC root that Windows clients use to open CAD files directly.
# E.g. \\192.168.1.37\plm-files  — must be a Windows share of the same
# directory that FILES_ROOT points to inside the container.
# Leave blank to disable the "Open" button.
FILES_UNC_ROOT: str = os.getenv("FILES_UNC_ROOT", "")

# Optional mapped drive letter that workstations use for the share above.
# E.g. if clients map \\192.168.1.37\plm-files as Z: set this to Z:
# When set, the plmopen:// URI uses the drive letter instead of the UNC path
# (some CAD apps handle drive letters more reliably than UNC).
FILES_MAPPED_DRIVE: str = os.getenv("FILES_MAPPED_DRIVE", "")

# ── Misc ─────────────────────────────────────────────────────────────────────
# Comma-separated email whitelist for Google OAuth mode. Empty = allow all.
ALLOWED_EMAILS: list[str] = [
    e.strip().lower()
    for e in os.getenv("ALLOWED_EMAILS", "").split(",")
    if e.strip()
]


def is_cad_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in CAD_EXTENSIONS


def google_redirect_uri() -> str:
    base = APP_BASE_URL.rstrip("/")
    return f"{base}/auth/google/callback"


def validate() -> list[str]:
    """Validate security-critical configuration.

    In production, fatal misconfigurations raise RuntimeError so the app
    refuses to start. In development they are returned as warnings instead,
    so local work is not blocked. Returns the list of non-fatal warnings.
    """
    fatal: list[str] = []
    warnings: list[str] = []

    if not SECRET_KEY or SECRET_KEY == _DEFAULT_SECRET_KEY:
        (fatal if IS_PRODUCTION else warnings).append(
            "SECRET_KEY is unset or using the built-in default — JWTs are forgeable. "
            "Generate one with: openssl rand -hex 32"
        )
    elif len(SECRET_KEY) < 32:
        (fatal if IS_PRODUCTION else warnings).append(
            "SECRET_KEY is shorter than 32 characters; use at least 32."
        )

    if IS_PRODUCTION and not APP_BASE_URL.startswith("https"):
        warnings.append(
            "APP_BASE_URL is not https in production; the session cookie will not be marked Secure."
        )

    if AUTH_MODE == "google" and not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        (fatal if IS_PRODUCTION else warnings).append(
            "AUTH_MODE=google but GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set."
        )

    if fatal:
        raise RuntimeError(
            "Refusing to start due to insecure configuration:\n  - "
            + "\n  - ".join(fatal)
            + "\nSet these in the environment (see .env.example) and restart."
        )
    return warnings
