"""Settings for the fact-checker. Everything can be changed with an environment
variable in Railway, without touching code."""
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _num(name, default):
    try:
        v = float(os.environ.get(name, ""))
        return v if v >= 0 else default
    except ValueError:
        return default


def _flag(name, default=True):
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


# Storage. On Railway DATA_DIR should point at the persistent volume, or the
# source library is wiped on every deploy.
DATA_DIR = os.environ.get("DATA_DIR") or os.path.join(_REPO_ROOT, "data")
DATA_DIR_IS_PERSISTENT = bool(os.environ.get("DATA_DIR"))
FC_DIR = os.path.join(DATA_DIR, "factcheck")
DB_PATH = os.path.join(FC_DIR, "library.db")
FILES_DIR = os.path.join(FC_DIR, "files")

# Models. The pilot traces with Sonnet; Opus is the one retry for a central claim that ends badly.
TRACE_MODEL = os.environ.get("FACTCHECK_TRACE_MODEL", "claude-sonnet-5-5")
EXTRACT_MODEL = os.environ.get("FACTCHECK_EXTRACT_MODEL", TRACE_MODEL)
CONTEXT_MODEL = os.environ.get("FACTCHECK_CONTEXT_MODEL", TRACE_MODEL)
RETRY_MODEL = os.environ.get("FACTCHECK_RETRY_MODEL", "claude-opus-5-5")
OPUS_RETRY = _flag("FACTCHECK_OPUS_RETRY", True)

# Budgets per claim. The central claim gets the full budget, claims 2 and 3 get less.
MAX_CLAIMS = 1  # v0.6: one central claim per video
CENTRAL_HOPS = int(_num("FACTCHECK_CENTRAL_HOPS", 6))
CENTRAL_READS = int(_num("FACTCHECK_CENTRAL_READS", 12))
OTHER_HOPS = int(_num("FACTCHECK_OTHER_HOPS", 4))
OTHER_READS = int(_num("FACTCHECK_OTHER_READS", 8))
CONTEXT_READS = int(_num("FACTCHECK_CONTEXT_READS", 6))
PAGE_CHARS = 6000          # how much of a page a tracer sees per read (it can search the rest)
MAX_VIDEO_SECONDS = 15 * 60
MAX_FRAMES = 16

# Spending guard (shared by every paid call in the fact-checker).
DAILY_CHECK_LIMIT = int(_num("FACTCHECK_DAILY_LIMIT", 15))
MONTHLY_BUDGET_USD = _num("FACTCHECK_MONTHLY_BUDGET_USD", 15)
MONTHLY_LOOKUP_LIMIT = int(_num("FACTCHECK_MONTHLY_LOOKUP_LIMIT", 300))

# Keys (all optional except Anthropic; each missing one is reported in Settings).
def anthropic_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

def scrape_key():
    return os.environ.get("SCRAPECREATORS_API_KEY", "")

def groq_key():
    return os.environ.get("GROQ_API_KEY", "")

def factcheck_api_key():
    return os.environ.get("GOOGLE_FACTCHECK_API_KEY", "")

def password():
    return os.environ.get("FACTCHECK_PASSWORD", "")
