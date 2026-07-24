from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ENV_DIR = ROOT / "edge_core" / "env"
TEMPLATE_NAMES = [
    ".env.example",
    ".env.cam01.example",
    ".env.cam02.example",
]


def _load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise AssertionError(f"invalid env line in {path}:{line_no}: {raw_line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise AssertionError(f"empty env key in {path}:{line_no}")
        if key in data:
            raise AssertionError(f"duplicate env key {key!r} in {path}")
        data[key] = value
    return data


def test_env_templates_exist_and_share_the_same_contract() -> None:
    parsed = {}
    for name in TEMPLATE_NAMES:
        path = ENV_DIR / name
        assert path.is_file(), f"missing env template: {path}"
        parsed[name] = _load_env_file(path)

    key_sets = {frozenset(data) for data in parsed.values()}
    assert len(key_sets) == 1

    common = parsed[".env.example"]
    assert common["EDGE_CAMERA_ID"] == "camXX"
    assert common["EDGE_MONITOR_SERVICE_NAME"] == "edge-camXX"
    assert common["EDGE_RTSP_URL"] == "rtsp://example.local/camXX"
    assert common["EDGE_FILE_PATH"] == "./data/samples/example_camXX.mp4"
    assert common["EDGE_STREAMING_URL"] == "rtmp://127.0.0.1:1935/live/camXX"


def test_camera_templates_only_change_camera_specific_values() -> None:
    cam01 = _load_env_file(ENV_DIR / ".env.cam01.example")
    cam02 = _load_env_file(ENV_DIR / ".env.cam02.example")

    shared_keys = [
        "EDGE_LOG_LEVEL",
        "MONITOR_ENDPOINT",
        "EDGE_POLL_INTERVAL",
        "EDGE_RETRY_BACKOFF",
        "EDGE_MODELS_CONFIG",
        "EDGE_INGEST_MODE",
        "EDGE_MODEL_NAME",
        "EDGE_MODEL_PATH",
        "EDGE_MODEL_DEVICE",
        "EDGE_CONF_THRESHOLD",
        "EDGE_TRACKER_CONFIG",
        "EDGE_MODE_DEFAULT",
        "EDGE_MODE_STRATEGY",
        "EDGE_APP_INBOUND_BACKEND",
        "EDGE_PHASE_ENABLED",
        "EDGE_PHASE_CHANNEL",
        "EDGE_PHASE_RESOURCE_NAME",
        "EDGE_EVENTS_BACKEND",
        "EDGE_EVENTS_CHANNEL",
        "EDGE_MATCHING_RESULT_ENABLED",
        "EDGE_MATCHING_RESULT_CHANNEL",
        "EDGE_MATCHING_RESULT_RESOURCE_NAME",
        "EDGE_STREAMING_ENABLED",
        "EDGE_STREAMING_STRATEGY",
        "EDGE_STREAMING_QUEUE_SIZE",
        "EDGE_STREAMING_IDLE_TIMEOUT",
        "EDGE_STREAMING_RESTART_BACKOFF",
        "EDGE_STREAMING_OUT_WIDTH",
        "EDGE_STREAMING_OUT_HEIGHT",
        "INTEGRATION_API_BASE",
        "INTEGRATION_API_TIMEOUT",
        "EDGE_HEALTH_SERVER_ENABLED",
        "EDGE_HEALTH_SERVER_HOST",
        "EDGE_HEALTH_SERVER_PORT",
    ]
    for key in shared_keys:
        assert cam01[key] == cam02[key], key

    camera_specific_keys = [
        "EDGE_CAMERA_ID",
        "EDGE_MONITOR_SERVICE_NAME",
        "EDGE_RTSP_URL",
        "EDGE_FILE_PATH",
        "EDGE_STREAMING_URL",
    ]
    for key in camera_specific_keys:
        assert cam01[key] != cam02[key], key

    assert cam01["EDGE_CAMERA_ID"] == "cam01"
    assert cam02["EDGE_CAMERA_ID"] == "cam02"
    assert cam01["EDGE_MONITOR_SERVICE_NAME"] == "edge-cam01"
    assert cam02["EDGE_MONITOR_SERVICE_NAME"] == "edge-cam02"
