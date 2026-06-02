import os
import sys
import logging
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlsplit, parse_qsl, urlunsplit
from importlib.resources import files

from dotenv import find_dotenv, load_dotenv


# Locked profile: set to a profile name (e.g., "astronomer") to lock the app
# to that profile and disable all profile switching. Leave as None for normal behavior.
LOCKED_PROFILE: str | None = None
PROJECT_ROOT = Path(__file__).parents[2].resolve()


def _is_source_checkout_root(root: Path) -> bool:
    """Return whether the given root looks like this project's source checkout."""
    return (root / "pyproject.toml").is_file() and (root / "src" / "xiaoze_conversation_app").is_dir()


def _packaged_profiles_directory() -> Path | None:
    """Return the installed wheel's packaged profiles directory when available."""
    try:
        return Path(str(files("xiaoze_talk_data").joinpath("profiles")))
    except Exception:
        return None


def _resolve_default_profiles_directory() -> Path:
    """Resolve built-in profiles from source checkout or installed package data."""
    source_profiles = PROJECT_ROOT / "profiles"
    if _is_source_checkout_root(PROJECT_ROOT) and source_profiles.is_dir():
        return source_profiles

    packaged_profiles = _packaged_profiles_directory()
    if packaged_profiles is not None and packaged_profiles.is_dir():
        return packaged_profiles

    return source_profiles


DEFAULT_PROFILES_DIRECTORY = _resolve_default_profiles_directory()

# Full list of voices supported by the OpenAI Realtime / TTS API.
# Source: https://developers.openai.com/api/docs/guides/text-to-speech/#voice-options
# "marin" and "cedar" are recommended for gpt-realtime-2.
AVAILABLE_VOICES: list[str] = [
    "alloy",
    "ash",
    "ballad",
    "cedar",
    "coral",
    "echo",
    "marin",
    "sage",
    "shimmer",
    "verse",
]
OPENAI_DEFAULT_VOICE = "cedar"

# Qwen3-TTS CustomVoice speaker catalog from the deployed Hugging Face backend.
HF_AVAILABLE_VOICES: list[str] = [
    "Aiden",
    "Ryan",
    "Dylan",
    "Eric",
    "Ono_Anna",
    "Serena",
    "Sohee",
    "Uncle_Fu",
    "Vivian",
]

# Voices supported by the Gemini Live API
GEMINI_AVAILABLE_VOICES: list[str] = [
    "Aoede",
    "Charon",
    "Fenrir",
    "Kore",
    "Leda",
    "Orus",
    "Puck",
    "Zephyr",
]

# Voices supported by the Aliyun Bailian DashScope API (Qwen3.5-Omni)
ALIYUN_AVAILABLE_VOICES: list[str] = [
    "Ethan", "Cherry", "Serena", "Chelsie", "Maia", "Vivian",
    "Momo", "Nini", "Stella", "Ono Anna", "Seren", "Mia",
    "Bella", "Bunny", "Bellona", "Katerina", "Elias", "Sohee",
    "Ebana", "Ryan", "Moon", "Kai", "Nofish", "Neil",
    "Andre", "Lenn", "Vincent", "Eldric Sage", "Arthur",
    "Mochi", "Dylan",
]


def _normalize_tts_voice(provider: str | None, voice: str | None) -> str:
    """Return a provider-compatible TTS voice."""
    normalized_provider = (provider or "").strip().lower()
    normalized_voice = (voice or "").strip()
    if normalized_provider == "aliyun":
        return normalized_voice if normalized_voice in ALIYUN_AVAILABLE_VOICES else "Ethan"
    return normalized_voice

OPENAI_BACKEND = "openai"
OPENAI_COMPATIBLE_BACKEND = "openai_compatible"
OPENAI_COMPATIBLE_CHAT_BACKEND = "openai_compatible_chat"
PLATFORM_AGENT_BACKEND = "platform_agent"
GEMINI_BACKEND = "gemini"
HF_BACKEND = "huggingface"
COMPOSED_BACKEND = "composed"
ALIYUN_BACKEND = "aliyun"
ALIYUN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_BACKEND_PROVIDER = COMPOSED_BACKEND
HF_REALTIME_CONNECTION_MODE_ENV = "HF_REALTIME_CONNECTION_MODE"
HF_REALTIME_WS_URL_ENV = "HF_REALTIME_WS_URL"
HF_LOCAL_CONNECTION_MODE = "local"
HF_DEPLOYED_CONNECTION_MODE = "deployed"
HF_REALTIME_SESSION_PROXY_URL = "https://pollen-robotics-reachy-mini-realtime-url.hf.space/session"


@dataclass(frozen=True)
class HFBackendDefaults:
    """Defaults for the Hugging Face realtime backend."""

    connection_mode: str = HF_DEPLOYED_CONNECTION_MODE
    # App-managed Hugging Face Space proxy. The Space forwards to the current
    # session allocator, so allocator changes do not require app releases.
    # Users who need a custom target should use HF_REALTIME_CONNECTION_MODE=local
    # with HF_REALTIME_WS_URL.
    session_url: str = HF_REALTIME_SESSION_PROXY_URL
    voice: str = "Aiden"
    model_name: str = ""
    direct_port: int = 8765


HF_DEFAULTS = HFBackendDefaults()
DEFAULT_MODEL_NAME_BY_BACKEND = {
    OPENAI_BACKEND: "gpt-realtime-2",
    OPENAI_COMPATIBLE_BACKEND: "",
    OPENAI_COMPATIBLE_CHAT_BACKEND: "",
    PLATFORM_AGENT_BACKEND: "",
    GEMINI_BACKEND: "gemini-3.1-flash-live-preview",
    HF_BACKEND: HF_DEFAULTS.model_name,
    COMPOSED_BACKEND: "",
    ALIYUN_BACKEND: "qwen3.5-omni-flash-realtime",
}
BACKEND_LABEL_BY_PROVIDER = {
    OPENAI_BACKEND: "OpenAI Realtime",
    OPENAI_COMPATIBLE_BACKEND: "OpenAI-compatible Realtime",
    OPENAI_COMPATIBLE_CHAT_BACKEND: "OpenAI-compatible Chat + ASR/TTS",
    PLATFORM_AGENT_BACKEND: "Platform Agent + ASR/TTS",
    GEMINI_BACKEND: "Gemini Live",
    HF_BACKEND: "Hugging Face",
    COMPOSED_BACKEND: "ASR+LLM+TTS 自由搭配",
    ALIYUN_BACKEND: "阿里云百炼",
}
COMPOSED_VOICE_DEFAULT = "alloy"
DEFAULT_VOICE_BY_BACKEND = {
    OPENAI_BACKEND: OPENAI_DEFAULT_VOICE,
    OPENAI_COMPATIBLE_BACKEND: OPENAI_DEFAULT_VOICE,
    OPENAI_COMPATIBLE_CHAT_BACKEND: OPENAI_DEFAULT_VOICE,
    PLATFORM_AGENT_BACKEND: OPENAI_DEFAULT_VOICE,
    GEMINI_BACKEND: "Kore",
    HF_BACKEND: HF_DEFAULTS.voice,
    COMPOSED_BACKEND: COMPOSED_VOICE_DEFAULT,
    ALIYUN_BACKEND: "Ethan",
}

logger = logging.getLogger(__name__)


def _is_gemini_model_name(model_name: str | None) -> bool:
    """Return True when the provided model name targets Gemini."""
    candidate = (model_name or "").strip().lower()
    return candidate.startswith("gemini")


def _normalize_backend_provider(
    backend_provider: str | None = None,
    model_name: str | None = None,
) -> str:
    """Normalize the configured backend provider."""
    candidate = (backend_provider or "").strip().lower()
    if candidate in DEFAULT_MODEL_NAME_BY_BACKEND:
        return candidate
    if candidate:
        expected = ", ".join(sorted(DEFAULT_MODEL_NAME_BY_BACKEND))
        raise ValueError(f"Invalid BACKEND_PROVIDER={backend_provider!r}. Expected one of: {expected}.")
    return GEMINI_BACKEND if _is_gemini_model_name(model_name) else DEFAULT_BACKEND_PROVIDER


def _resolve_model_name(
    backend_provider: str | None = None,
    model_name: str | None = None,
) -> str:
    """Return a model name that matches the selected backend provider."""
    normalized_backend = _normalize_backend_provider(backend_provider, model_name)
    if normalized_backend == HF_BACKEND:
        return DEFAULT_MODEL_NAME_BY_BACKEND[HF_BACKEND]

    candidate = (model_name or "").strip()
    if candidate:
        if normalized_backend == GEMINI_BACKEND and _is_gemini_model_name(candidate):
            return candidate
        if (
            normalized_backend
            in {
                OPENAI_BACKEND,
                OPENAI_COMPATIBLE_BACKEND,
                OPENAI_COMPATIBLE_CHAT_BACKEND,
                PLATFORM_AGENT_BACKEND,
                ALIYUN_BACKEND,
            }
            and not _is_gemini_model_name(candidate)
        ):
            return candidate
        logger.warning(
            "MODEL_NAME=%r does not match BACKEND_PROVIDER=%r, using default %r",
            candidate,
            normalized_backend,
            DEFAULT_MODEL_NAME_BY_BACKEND[normalized_backend],
        )
    return DEFAULT_MODEL_NAME_BY_BACKEND[normalized_backend]


def _env_flag(name: str, default: bool = False) -> bool:
    """Parse a boolean environment flag.

    Accepted truthy values: 1, true, yes, on
    Accepted falsy values: 0, false, no, off
    """
    raw = os.getenv(name)
    if raw is None:
        return default

    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False

    logger.warning("Invalid boolean value for %s=%r, using default=%s", name, raw, default)
    return default


def _normalize_hf_connection_mode(value: str | None) -> str | None:
    """Normalize the Hugging Face connection mode, if explicitly configured."""
    candidate = (value or "").strip().lower()
    if not candidate:
        return None

    if candidate not in {HF_LOCAL_CONNECTION_MODE, HF_DEPLOYED_CONNECTION_MODE}:
        logger.warning(
            "Invalid %s=%r. Expected local or deployed.",
            HF_REALTIME_CONNECTION_MODE_ENV,
            value,
        )
        return None
    return candidate


@dataclass(frozen=True)
class HFConnectionSelection:
    """Resolved Hugging Face connection mode and target availability."""

    mode: str
    has_target: bool
    session_url: str | None = None
    direct_ws_url: str | None = None


@dataclass(frozen=True)
class HFRealtimeURLParts:
    """Parsed Hugging Face realtime URL components used by UI and client setup."""

    base_url: str
    websocket_base_url: str
    connect_query: dict[str, str]
    host: str | None
    port: int | None
    has_realtime_path: bool


def parse_hf_realtime_url(realtime_url: str) -> HFRealtimeURLParts:
    """Parse a Hugging Face realtime URL into OpenAI-compatible client endpoints."""
    parsed = urlsplit(realtime_url)
    scheme = parsed.scheme.lower()
    if scheme not in {"ws", "wss", "http", "https"}:
        raise ValueError(
            "Expected Hugging Face realtime URL to start with ws://, wss://, http://, or https://, "
            f"got: {realtime_url}"
        )

    path = parsed.path.rstrip("/")
    has_realtime_path = path.endswith("/realtime")
    if has_realtime_path:
        base_path = path[: -len("/realtime")]
    else:
        base_path = path

    connect_query = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "model"}
    http_scheme = "https" if scheme in {"wss", "https"} else "http"
    websocket_scheme = "wss" if scheme in {"wss", "https"} else "ws"
    base_url = urlunsplit((http_scheme, parsed.netloc, base_path, "", ""))
    websocket_base_url = urlunsplit((websocket_scheme, parsed.netloc, base_path, "", ""))
    return HFRealtimeURLParts(
        base_url=base_url,
        websocket_base_url=websocket_base_url,
        connect_query=connect_query,
        host=parsed.hostname,
        port=parsed.port or HF_DEFAULTS.direct_port,
        has_realtime_path=has_realtime_path,
    )


def parse_hf_direct_target(ws_url: str | None) -> tuple[str | None, int | None]:
    """Extract host and port from a direct Hugging Face realtime URL."""
    if not ws_url:
        return None, None
    try:
        parsed = parse_hf_realtime_url(ws_url)
        return parsed.host, parsed.port
    except Exception:
        return None, None


def build_hf_direct_ws_url(host: str, port: int) -> str:
    """Build the direct Hugging Face realtime websocket URL used by the app."""
    return f"ws://{host}:{port}/v1/realtime"


def _collect_profile_names(profiles_root: Path) -> set[str]:
    """Return profile folder names from a profiles root directory."""
    if not profiles_root.exists() or not profiles_root.is_dir():
        return set()
    return {p.name for p in profiles_root.iterdir() if p.is_dir()}


def _collect_tool_module_names(tools_root: Path) -> set[str]:
    """Return tool module names from a tools directory."""
    if not tools_root.exists() or not tools_root.is_dir():
        return set()
    ignored = {"__init__", "core_tools"}
    return {p.stem for p in tools_root.glob("*.py") if p.is_file() and p.stem not in ignored}


def _raise_on_name_collisions(
    *,
    label: str,
    external_root: Path,
    internal_root: Path,
    external_names: set[str],
    internal_names: set[str],
) -> None:
    """Raise with a clear message when external/internal names collide."""
    collisions = sorted(external_names & internal_names)
    if not collisions:
        return

    raise RuntimeError(
        f"Config.__init__(): Ambiguous {label} names found in both external and built-in libraries: {collisions}. "
        f"External {label} root: {external_root}. Built-in {label} root: {internal_root}. "
        f"Please rename the conflicting external {label}(s) to continue."
    )


# Validate LOCKED_PROFILE at startup
if LOCKED_PROFILE is not None:
    _profiles_dir = DEFAULT_PROFILES_DIRECTORY
    _profile_path = _profiles_dir / LOCKED_PROFILE
    _instructions_file = _profile_path / "instructions.txt"
    if not _profile_path.is_dir():
        print(f"Error: LOCKED_PROFILE '{LOCKED_PROFILE}' does not exist in {_profiles_dir}", file=sys.stderr)
        sys.exit(1)
    if not _instructions_file.is_file():
        print(f"Error: LOCKED_PROFILE '{LOCKED_PROFILE}' has no instructions.txt", file=sys.stderr)
        sys.exit(1)

_skip_dotenv = _env_flag("REACHY_MINI_SKIP_DOTENV", default=False)

if _skip_dotenv:
    logger.info("Skipping .env loading because REACHY_MINI_SKIP_DOTENV is set")
else:
    # Locate .env file (search upward from current working directory)
    dotenv_path = find_dotenv(usecwd=True)

    if dotenv_path:
        # Load .env and override environment variables
        load_dotenv(dotenv_path=dotenv_path, override=True)
        logger.info(f"Configuration loaded from {dotenv_path}")
    else:
        logger.warning("No .env file found, using environment variables")


class Config:
    """Configuration class for the conversation app."""

    # Required (one of these depending on BACKEND_PROVIDER)
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # The key is downloaded in console.py if needed
    OPENAI_COMPATIBLE_API_KEY = os.getenv("OPENAI_COMPATIBLE_API_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")

    # Optional
    BACKEND_PROVIDER = _normalize_backend_provider(
        os.getenv("BACKEND_PROVIDER"),
        os.getenv("MODEL_NAME"),
    )
    MODEL_NAME = _resolve_model_name(BACKEND_PROVIDER, os.getenv("MODEL_NAME"))
    HF_REALTIME_CONNECTION_MODE = (
        _normalize_hf_connection_mode(os.getenv(HF_REALTIME_CONNECTION_MODE_ENV)) or HF_DEFAULTS.connection_mode
    )
    # Deliberately ignore HF_REALTIME_SESSION_URL from the environment; the app-managed proxy is HF_DEFAULTS.session_url.
    HF_REALTIME_SESSION_URL = HF_DEFAULTS.session_url
    HF_REALTIME_WS_URL = os.getenv(HF_REALTIME_WS_URL_ENV)
    HF_HOME = os.getenv("HF_HOME", "./cache")
    LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
    HF_TOKEN = os.getenv("HF_TOKEN")  # Optional, falls back to hf auth login if not set
    OPENAI_COMPATIBLE_BASE_URL = os.getenv("OPENAI_COMPATIBLE_BASE_URL")
    OPENAI_COMPATIBLE_WEBSOCKET_BASE_URL = os.getenv("OPENAI_COMPATIBLE_WEBSOCKET_BASE_URL")
    OPENAI_COMPATIBLE_CHAT_BASE_URL = os.getenv("OPENAI_COMPATIBLE_CHAT_BASE_URL")
    OPENAI_COMPATIBLE_ASR_BASE_URL = os.getenv("OPENAI_COMPATIBLE_ASR_BASE_URL")
    OPENAI_COMPATIBLE_TTS_BASE_URL = os.getenv("OPENAI_COMPATIBLE_TTS_BASE_URL")
    OPENAI_COMPATIBLE_VOICE = os.getenv("OPENAI_COMPATIBLE_VOICE")
    OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL = os.getenv("OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL", "gpt-4o-transcribe")
    OPENAI_COMPATIBLE_TTS_MODEL = os.getenv("OPENAI_COMPATIBLE_TTS_MODEL", "tts-1")
    OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT = os.getenv("OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT", "pcm")
    OPENAI_COMPATIBLE_TTS_SAMPLE_RATE = int(os.getenv("OPENAI_COMPATIBLE_TTS_SAMPLE_RATE", "24000"))
    OPENAI_COMPATIBLE_ASR_LANGUAGE = os.getenv("OPENAI_COMPATIBLE_ASR_LANGUAGE")
    OPENAI_COMPATIBLE_TURN_SILENCE_MS = int(os.getenv("OPENAI_COMPATIBLE_TURN_SILENCE_MS", "900"))
    OPENAI_COMPATIBLE_VAD_THRESHOLD = float(os.getenv("OPENAI_COMPATIBLE_VAD_THRESHOLD", "0.012"))

    # Per-component providers (used when BACKEND_PROVIDER = "composed")
    _fallback_url = OPENAI_COMPATIBLE_BASE_URL or ""
    ASR_PROVIDER = (os.getenv("ASR_PROVIDER") or "openai").strip().lower()
    ASR_BASE_URL = (os.getenv("ASR_BASE_URL") or _fallback_url or "").strip()
    ASR_MODEL = (os.getenv("ASR_MODEL") or "gpt-4o-transcribe").strip()
    ASR_LANGUAGE = (os.getenv("ASR_LANGUAGE") or "").strip()
    LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()
    LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or _fallback_url or "").strip()
    LLM_MODEL = (os.getenv("LLM_MODEL") or MODEL_NAME or "gpt-4o").strip()
    TTS_PROVIDER = (os.getenv("TTS_PROVIDER") or "openai").strip().lower()
    TTS_BASE_URL = (os.getenv("TTS_BASE_URL") or _fallback_url or "").strip()
    TTS_MODEL = (os.getenv("TTS_MODEL") or "tts-1").strip()
    TTS_VOICE = _normalize_tts_voice(TTS_PROVIDER, os.getenv("TTS_VOICE") or OPENAI_COMPATIBLE_VOICE)
    TTS_SAMPLE_RATE = int(os.getenv("TTS_SAMPLE_RATE", os.getenv("OPENAI_COMPATIBLE_TTS_SAMPLE_RATE", "24000")))
    TTS_RESPONSE_FORMAT = (os.getenv("TTS_RESPONSE_FORMAT") or os.getenv("OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT", "pcm")).strip().lower()
    VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", os.getenv("OPENAI_COMPATIBLE_VAD_THRESHOLD", "0.012")))
    TURN_SILENCE_MS = int(os.getenv("TURN_SILENCE_MS", os.getenv("OPENAI_COMPATIBLE_TURN_SILENCE_MS", "900")))

    logger.debug(
        "Backend provider: %s, Model: %s, HF mode: %s, HF session URL set: %s, HF direct URL set: %s, HF_HOME: %s, Vision Model: %s",
        BACKEND_PROVIDER,
        MODEL_NAME,
        HF_REALTIME_CONNECTION_MODE,
        bool(HF_REALTIME_SESSION_URL and HF_REALTIME_SESSION_URL.strip()),
        bool(HF_REALTIME_WS_URL and HF_REALTIME_WS_URL.strip()),
        HF_HOME,
        LOCAL_VISION_MODEL,
    )

    # Filesystem root containing profile directories, not a Python import path.
    _profiles_directory_env = os.getenv("REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY")
    PROFILES_DIRECTORY = Path(_profiles_directory_env) if _profiles_directory_env else DEFAULT_PROFILES_DIRECTORY
    _tools_directory_env = os.getenv("REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY")
    TOOLS_DIRECTORY = Path(_tools_directory_env) if _tools_directory_env else None
    AUTOLOAD_EXTERNAL_TOOLS = _env_flag("AUTOLOAD_EXTERNAL_TOOLS", default=False)
    REACHY_MINI_CUSTOM_PROFILE = LOCKED_PROFILE or os.getenv("REACHY_MINI_CUSTOM_PROFILE")

    logger.debug(f"Custom Profile: {REACHY_MINI_CUSTOM_PROFILE}")

    def __init__(self) -> None:
        """Initialize the configuration."""
        if self.REACHY_MINI_CUSTOM_PROFILE and self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            selected_profile_path = self.PROFILES_DIRECTORY / self.REACHY_MINI_CUSTOM_PROFILE
            if not selected_profile_path.is_dir():
                available_profiles = sorted(_collect_profile_names(self.PROFILES_DIRECTORY))
                raise RuntimeError(
                    "Config.__init__(): Selected profile "
                    f"'{self.REACHY_MINI_CUSTOM_PROFILE}' was not found in external profiles root "
                    f"{self.PROFILES_DIRECTORY}. "
                    f"Available external profiles: {available_profiles}. "
                    "Either set 'REACHY_MINI_CUSTOM_PROFILE' to one of the available external profiles "
                    "or unset 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' to use built-in profiles."
                )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            external_profiles = _collect_profile_names(self.PROFILES_DIRECTORY)
            internal_profiles = _collect_profile_names(DEFAULT_PROFILES_DIRECTORY)
            _raise_on_name_collisions(
                label="profile",
                external_root=self.PROFILES_DIRECTORY,
                internal_root=DEFAULT_PROFILES_DIRECTORY,
                external_names=external_profiles,
                internal_names=internal_profiles,
            )

        if self.TOOLS_DIRECTORY is not None:
            builtin_tools_root = Path(__file__).parent / "tools"
            external_tools = _collect_tool_module_names(self.TOOLS_DIRECTORY)
            internal_tools = _collect_tool_module_names(builtin_tools_root)
            _raise_on_name_collisions(
                label="tool",
                external_root=self.TOOLS_DIRECTORY,
                internal_root=builtin_tools_root,
                external_names=external_tools,
                internal_names=internal_tools,
            )

        if self.PROFILES_DIRECTORY != DEFAULT_PROFILES_DIRECTORY:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is set. "
                "Profiles (instructions.txt, ...) will be loaded from %s.",
                self.PROFILES_DIRECTORY,
            )
        else:
            logger.info(
                "'REACHY_MINI_EXTERNAL_PROFILES_DIRECTORY' is not set. Using built-in profiles from %s.",
                DEFAULT_PROFILES_DIRECTORY,
            )

        if self.TOOLS_DIRECTORY is not None:
            logger.warning(
                "Environment variable 'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is set. "
                "External tools will be loaded from %s.",
                self.TOOLS_DIRECTORY,
            )
        else:
            logger.info("'REACHY_MINI_EXTERNAL_TOOLS_DIRECTORY' is not set. Using built-in shared tools only.")


config = Config()


def refresh_runtime_config_from_env() -> None:
    """Refresh mutable runtime config fields from the current environment."""
    config.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    config.OPENAI_COMPATIBLE_API_KEY = os.getenv("OPENAI_COMPATIBLE_API_KEY")
    config.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    config.ALIYUN_API_KEY = os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    config.BACKEND_PROVIDER = _normalize_backend_provider(
        os.getenv("BACKEND_PROVIDER"),
        os.getenv("MODEL_NAME"),
    )
    config.MODEL_NAME = _resolve_model_name(config.BACKEND_PROVIDER, os.getenv("MODEL_NAME"))
    config.HF_REALTIME_CONNECTION_MODE = (
        _normalize_hf_connection_mode(os.getenv(HF_REALTIME_CONNECTION_MODE_ENV)) or HF_DEFAULTS.connection_mode
    )
    # Deliberately ignore HF_REALTIME_SESSION_URL from the environment; the app-managed proxy is HF_DEFAULTS.session_url.
    config.HF_REALTIME_SESSION_URL = HF_DEFAULTS.session_url
    config.HF_REALTIME_WS_URL = os.getenv(HF_REALTIME_WS_URL_ENV)
    config.HF_HOME = os.getenv("HF_HOME", "./cache")
    config.LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "HuggingFaceTB/SmolVLM2-2.2B-Instruct")
    config.HF_TOKEN = os.getenv("HF_TOKEN")
    config.OPENAI_COMPATIBLE_BASE_URL = os.getenv("OPENAI_COMPATIBLE_BASE_URL")
    config.OPENAI_COMPATIBLE_WEBSOCKET_BASE_URL = os.getenv("OPENAI_COMPATIBLE_WEBSOCKET_BASE_URL")
    config.OPENAI_COMPATIBLE_CHAT_BASE_URL = os.getenv("OPENAI_COMPATIBLE_CHAT_BASE_URL")
    config.OPENAI_COMPATIBLE_ASR_BASE_URL = os.getenv("OPENAI_COMPATIBLE_ASR_BASE_URL")
    config.OPENAI_COMPATIBLE_TTS_BASE_URL = os.getenv("OPENAI_COMPATIBLE_TTS_BASE_URL")
    config.OPENAI_COMPATIBLE_VOICE = os.getenv("OPENAI_COMPATIBLE_VOICE")
    config.OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL = os.getenv(
        "OPENAI_COMPATIBLE_TRANSCRIPTION_MODEL",
        "gpt-4o-transcribe",
    )
    config.OPENAI_COMPATIBLE_TTS_MODEL = os.getenv("OPENAI_COMPATIBLE_TTS_MODEL", "tts-1")
    config.OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT = os.getenv("OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT", "pcm")
    config.OPENAI_COMPATIBLE_TTS_SAMPLE_RATE = int(os.getenv("OPENAI_COMPATIBLE_TTS_SAMPLE_RATE", "24000"))
    config.OPENAI_COMPATIBLE_ASR_LANGUAGE = os.getenv("OPENAI_COMPATIBLE_ASR_LANGUAGE")
    config.OPENAI_COMPATIBLE_TURN_SILENCE_MS = int(os.getenv("OPENAI_COMPATIBLE_TURN_SILENCE_MS", "900"))
    config.OPENAI_COMPATIBLE_VAD_THRESHOLD = float(os.getenv("OPENAI_COMPATIBLE_VAD_THRESHOLD", "0.012"))
    config.REACHY_MINI_CUSTOM_PROFILE = LOCKED_PROFILE or os.getenv("REACHY_MINI_CUSTOM_PROFILE")

    # Per-component runtime refresh
    _rt_fallback = config.OPENAI_COMPATIBLE_BASE_URL or ""
    config.ASR_PROVIDER = (os.getenv("ASR_PROVIDER") or "openai").strip().lower()
    config.ASR_BASE_URL = (os.getenv("ASR_BASE_URL") or _rt_fallback or "").strip()
    config.ASR_MODEL = (os.getenv("ASR_MODEL") or "gpt-4o-transcribe").strip()
    config.ASR_LANGUAGE = (os.getenv("ASR_LANGUAGE") or "").strip()
    config.LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "openai").strip().lower()
    config.LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or _rt_fallback or "").strip()
    config.LLM_MODEL = (os.getenv("LLM_MODEL") or config.MODEL_NAME or "gpt-4o").strip()
    config.TTS_PROVIDER = (os.getenv("TTS_PROVIDER") or "openai").strip().lower()
    config.TTS_BASE_URL = (os.getenv("TTS_BASE_URL") or _rt_fallback or "").strip()
    config.TTS_MODEL = (os.getenv("TTS_MODEL") or "tts-1").strip()
    config.TTS_VOICE = _normalize_tts_voice(
        config.TTS_PROVIDER,
        os.getenv("TTS_VOICE") or getattr(config, "OPENAI_COMPATIBLE_VOICE", ""),
    )
    config.TTS_SAMPLE_RATE = int(os.getenv("TTS_SAMPLE_RATE", os.getenv("OPENAI_COMPATIBLE_TTS_SAMPLE_RATE", "24000")))
    config.TTS_RESPONSE_FORMAT = (os.getenv("TTS_RESPONSE_FORMAT") or os.getenv("OPENAI_COMPATIBLE_TTS_RESPONSE_FORMAT", "pcm")).strip().lower()
    config.VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", os.getenv("OPENAI_COMPATIBLE_VAD_THRESHOLD", "0.012")))
    config.TURN_SILENCE_MS = int(os.getenv("TURN_SILENCE_MS", os.getenv("OPENAI_COMPATIBLE_TURN_SILENCE_MS", "900")))


def get_backend_choice(model_name: str | None = None) -> str:
    """Return the configured backend family."""
    if model_name is not None:
        return _normalize_backend_provider(model_name=model_name)
    return _normalize_backend_provider(config.BACKEND_PROVIDER, config.MODEL_NAME)


def get_model_name_for_backend(backend: str) -> str:
    """Return the default model name for a backend selector value."""
    return DEFAULT_MODEL_NAME_BY_BACKEND[_normalize_backend_provider(backend)]


def get_backend_label(backend: str | None = None) -> str:
    """Return a human-readable label for a backend selector value."""
    normalized_backend = get_backend_choice() if backend is None else _normalize_backend_provider(backend)
    return BACKEND_LABEL_BY_PROVIDER[normalized_backend]


def get_available_voices_for_backend(backend: str | None = None) -> list[str]:
    """Return the curated voice list for a backend selector value."""
    normalized_backend = get_backend_choice() if backend is None else _normalize_backend_provider(backend)
    if normalized_backend == GEMINI_BACKEND:
        return list(GEMINI_AVAILABLE_VOICES)
    if normalized_backend == HF_BACKEND:
        return list(HF_AVAILABLE_VOICES)
    if normalized_backend == ALIYUN_BACKEND:
        return list(ALIYUN_AVAILABLE_VOICES)
    if normalized_backend == COMPOSED_BACKEND:
        tts_provider = (os.getenv("TTS_PROVIDER") or "openai").strip().lower()
        if tts_provider == "aliyun":
            return list(ALIYUN_AVAILABLE_VOICES)
    if normalized_backend in {OPENAI_COMPATIBLE_BACKEND, OPENAI_COMPATIBLE_CHAT_BACKEND, PLATFORM_AGENT_BACKEND, COMPOSED_BACKEND}:
        configured_voice = (getattr(config, "TTS_VOICE", None) or getattr(config, "OPENAI_COMPATIBLE_VOICE", None) or "").strip()
        voices = list(AVAILABLE_VOICES)
        if configured_voice and configured_voice not in voices:
            voices.insert(0, configured_voice)
        return voices
    return list(AVAILABLE_VOICES)


def get_default_voice_for_backend(backend: str | None = None) -> str:
    """Return the default voice for a backend selector value."""
    normalized_backend = get_backend_choice() if backend is None else _normalize_backend_provider(backend)
    if normalized_backend == ALIYUN_BACKEND:
        return "Ethan"
    if normalized_backend == COMPOSED_BACKEND:
        tts_provider = (os.getenv("TTS_PROVIDER") or "openai").strip().lower()
        if tts_provider == "aliyun":
            return "Ethan"
    if normalized_backend in {OPENAI_COMPATIBLE_BACKEND, OPENAI_COMPATIBLE_CHAT_BACKEND, PLATFORM_AGENT_BACKEND, COMPOSED_BACKEND}:
        configured_voice = (getattr(config, "TTS_VOICE", None) or getattr(config, "OPENAI_COMPATIBLE_VOICE", None) or "").strip()
        return configured_voice or DEFAULT_VOICE_BY_BACKEND[normalized_backend]
    return DEFAULT_VOICE_BY_BACKEND[normalized_backend]


def get_hf_session_url() -> str | None:
    """Return the built-in Hugging Face session proxy URL, if any."""
    value = (getattr(config, "HF_REALTIME_SESSION_URL", None) or "").strip()
    return value or None


def get_hf_direct_ws_url() -> str | None:
    """Return the configured direct Hugging Face realtime URL, if any."""
    value = (getattr(config, "HF_REALTIME_WS_URL", None) or "").strip()
    return value or None


def get_hf_connection_selection() -> HFConnectionSelection:
    """Resolve the selected Hugging Face connection mode and whether it is usable."""
    session_url = get_hf_session_url()
    direct_ws_url = get_hf_direct_ws_url()
    mode = _normalize_hf_connection_mode(getattr(config, "HF_REALTIME_CONNECTION_MODE", None))
    if mode is None:
        raise RuntimeError(f"{HF_REALTIME_CONNECTION_MODE_ENV} must be set to local or deployed.")

    target = direct_ws_url if mode == HF_LOCAL_CONNECTION_MODE else session_url

    return HFConnectionSelection(
        mode=mode,
        has_target=bool(target),
        session_url=session_url,
        direct_ws_url=direct_ws_url,
    )


def has_hf_realtime_target() -> bool:
    """Return whether Hugging Face has a target for the selected mode."""
    return get_hf_connection_selection().has_target


def is_gemini_model() -> bool:
    """Return True if the configured MODEL_NAME is a Gemini Live model."""
    return get_backend_choice() == GEMINI_BACKEND


def set_custom_profile(profile: str | None) -> None:
    """Update the selected custom profile at runtime and expose it via env.

    This ensures modules that read `config` and code that inspects the
    environment see a consistent value.
    """
    if LOCKED_PROFILE is not None:
        return
    try:
        config.REACHY_MINI_CUSTOM_PROFILE = profile
    except Exception:
        pass
    try:
        import os as _os

        if profile:
            _os.environ["REACHY_MINI_CUSTOM_PROFILE"] = profile
        else:
            # Remove to reflect default
            _os.environ.pop("REACHY_MINI_CUSTOM_PROFILE", None)
    except Exception:
        pass
