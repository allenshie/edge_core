"""Edge 模組設定讀取。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).lower() not in {"0", "false", "no"}


def _get_env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value
    return default


def _normalize_backend(value: str | None, default: str) -> str:
    return (value or default).strip().lower()


def _normalize_channel(backend: str, value: str | None, default: str) -> str:
    channel = (value or default).strip() or default
    if backend == "http":
        return channel if channel.startswith("/") else f"/{channel}"
    return channel[1:] if channel.startswith("/") else channel


@dataclass
class CameraConfig:
    camera_id: str = field(default_factory=lambda: os.environ.get("EDGE_CAMERA_ID", "cam01"))


@dataclass
class ModelConfig:
    name: str = field(default_factory=lambda: os.environ.get("EDGE_MODEL_NAME", "yolo11n"))
    weights_path: str = field(
        default_factory=lambda: os.environ.get("EDGE_MODEL_PATH", "./A6_PN001_20250806_yolov12s-dec_v1.pt")
    )
    confidence_threshold: float = field(default_factory=lambda: float(os.environ.get("EDGE_CONF_THRESHOLD", "0.5")))
    device: str | None = field(default_factory=lambda: os.environ.get("EDGE_MODEL_DEVICE"))
    visualize: bool = field(
        default_factory=lambda: _to_bool(os.environ.get("EDGE_MODEL_VISUALIZE"), True)
    )
    tracker_config: str | None = field(
        default_factory=lambda: os.environ.get("EDGE_TRACKER_CONFIG", "trackers/bytetrack.yaml")
    )

    def resolve_tracker_config(self, project_root: Path | None = None) -> str | None:
        cfg = (self.tracker_config or "").strip()
        if not cfg:
            return None
        cfg_path = Path(cfg)
        if cfg_path.is_absolute():
            return str(cfg_path)
        search_root = project_root or Path(__file__).resolve().parents[2]
        candidate = (search_root / cfg_path).resolve()
        if candidate.exists():
            return str(candidate)
        return cfg


@dataclass
class VisualizationConfig:
    enabled: bool = field(
        default_factory=lambda: _to_bool(
            os.environ.get("EDGE_VISUAL_ENABLED"),
            _to_bool(os.environ.get("EDGE_MODEL_VISUALIZE"), True),
        )
    )
    mode: str = field(default_factory=lambda: os.environ.get("EDGE_VISUAL_MODE", "write").strip().lower())
    window_name: str = field(default_factory=lambda: os.environ.get("EDGE_VISUAL_WINDOW", "edge-preview"))
    window_width: int = field(default_factory=lambda: int(os.environ.get("EDGE_VISUAL_WIDTH", "1280")))
    window_height: int = field(default_factory=lambda: int(os.environ.get("EDGE_VISUAL_HEIGHT", "720")))


@dataclass
class RtspConfig:
    url: str = field(default_factory=lambda: os.environ.get("EDGE_RTSP_URL", "rtsp://localhost:554/stream"))
    drop_frames: int = field(default_factory=lambda: int(os.environ.get("EDGE_RTSP_DROP_FRAMES", "2")))
    reconnect_seconds: float = field(default_factory=lambda: float(os.environ.get("EDGE_RTSP_RECONNECT", "1")))
    fps: float = field(default_factory=lambda: float(os.environ.get("EDGE_RTSP_FPS", "30")))
    frame_width: int | None = field(
        default_factory=lambda: int(os.environ.get("EDGE_RTSP_WIDTH")) if os.environ.get("EDGE_RTSP_WIDTH") else None
    )
    frame_height: int | None = field(
        default_factory=lambda: int(os.environ.get("EDGE_RTSP_HEIGHT")) if os.environ.get("EDGE_RTSP_HEIGHT") else None
    )


@dataclass
class IntegrationConfig:
    api_base: str = field(default_factory=lambda: os.environ.get("INTEGRATION_API_BASE", "http://localhost:9000"))
    timeout_seconds: int = field(default_factory=lambda: int(os.environ.get("INTEGRATION_API_TIMEOUT", "5")))


@dataclass
class MqttConfig:
    enabled: bool = field(default_factory=lambda: _to_bool(os.environ.get("EDGE_MQTT_ENABLED"), False))
    host: str = field(default_factory=lambda: os.environ.get("EDGE_MQTT_HOST", "localhost"))
    port: int = field(default_factory=lambda: int(os.environ.get("EDGE_MQTT_PORT", "1883")))
    qos: int = field(default_factory=lambda: int(os.environ.get("EDGE_MQTT_QOS", "1")))
    client_id: str | None = field(default_factory=lambda: os.environ.get("EDGE_MQTT_CLIENT_ID"))
    auth_enabled: bool = field(default_factory=lambda: _to_bool(os.environ.get("EDGE_MQTT_AUTH_ENABLED"), False))
    username: str | None = field(default_factory=lambda: os.environ.get("EDGE_MQTT_USERNAME"))
    password: str | None = field(default_factory=lambda: os.environ.get("EDGE_MQTT_PASSWORD"))


@dataclass
class HttpMessagingConfig:
    listen_host: str = field(default_factory=lambda: os.environ.get("EDGE_HTTP_LISTEN_HOST", "0.0.0.0"))
    listen_port: int = field(default_factory=lambda: int(os.environ.get("EDGE_HTTP_LISTEN_PORT", "9000")))


@dataclass
class PhaseMessagingConfig:
    backend: str = field(
        default_factory=lambda: _normalize_backend(os.environ.get("EDGE_PHASE_BACKEND"), "none")
    )
    channel: str = field(default_factory=lambda: os.environ.get("EDGE_PHASE_CHANNEL", ""))

    def __post_init__(self) -> None:
        if self.backend == "none" and _to_bool(os.environ.get("EDGE_MQTT_ENABLED"), False):
            self.backend = "mqtt"
        default_channel = "/integration/phase" if self.backend == "http" else "integration/phase"
        self.channel = _normalize_channel(self.backend, self.channel, default_channel)


@dataclass
class EdgeEventMessagingConfig:
    backend: str = field(
        default_factory=lambda: _normalize_backend(os.environ.get("EDGE_EVENTS_BACKEND"), "http")
    )
    channel: str = field(default_factory=lambda: os.environ.get("EDGE_EVENTS_CHANNEL", ""))

    def __post_init__(self) -> None:
        default_channel = "/edge/events" if self.backend == "http" else "edge/events"
        self.channel = _normalize_channel(self.backend, self.channel, default_channel)


@dataclass
class FileSourceConfig:
    path: str | None = field(default_factory=lambda: os.environ.get("EDGE_FILE_PATH"))
    loop: bool = field(default_factory=lambda: _to_bool(os.environ.get("EDGE_FILE_LOOP"), True))
    drop_frames: int = field(
        default_factory=lambda: int(
            os.environ.get("EDGE_FILE_DROP_FRAMES", os.environ.get("EDGE_RTSP_DROP_FRAMES", "0"))
        )
    )
    fps: float | None = field(
        default_factory=lambda: float(os.environ["EDGE_FILE_FPS"]) if os.environ.get("EDGE_FILE_FPS") else None
    )


@dataclass
class CameraSourceConfig:
    device: int = field(default_factory=lambda: int(os.environ.get("EDGE_CAMERA_DEVICE", "0")))
    drop_frames: int = field(default_factory=lambda: int(os.environ.get("EDGE_CAMERA_DROP_FRAMES", "0")))
    fps: float | None = field(
        default_factory=lambda: float(os.environ["EDGE_CAMERA_FPS"]) if os.environ.get("EDGE_CAMERA_FPS") else None
    )
    frame_width: int | None = field(
        default_factory=lambda: int(os.environ.get("EDGE_CAMERA_WIDTH"))
        if os.environ.get("EDGE_CAMERA_WIDTH")
        else None
    )
    frame_height: int | None = field(
        default_factory=lambda: int(os.environ.get("EDGE_CAMERA_HEIGHT"))
        if os.environ.get("EDGE_CAMERA_HEIGHT")
        else None
    )


@dataclass
class IngestionConfig:
    mode: str = field(default_factory=lambda: (os.environ.get("EDGE_INGEST_MODE", "rtsp") or "rtsp").strip().lower())
    rtsp: RtspConfig = field(default_factory=RtspConfig)
    file: FileSourceConfig = field(default_factory=FileSourceConfig)
    camera: CameraSourceConfig = field(default_factory=CameraSourceConfig)


@dataclass
class StreamingConfig:
    enabled: bool = field(default_factory=lambda: _to_bool(os.environ.get("EDGE_STREAMING_ENABLED"), False))
    strategy: str = field(default_factory=lambda: os.environ.get("EDGE_STREAMING_STRATEGY", "cpu").strip().lower())
    url: str = field(default_factory=lambda: os.environ.get("EDGE_STREAMING_URL", "").strip())
    queue_size: int = field(default_factory=lambda: int(os.environ.get("EDGE_STREAMING_QUEUE_SIZE", "30")))
    idle_timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("EDGE_STREAMING_IDLE_TIMEOUT", "3"))
    )
    restart_backoff_seconds: float = field(
        default_factory=lambda: float(os.environ.get("EDGE_STREAMING_RESTART_BACKOFF", "1"))
    )


@dataclass
class EdgeConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    visualization: VisualizationConfig = field(default_factory=VisualizationConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    integration: IntegrationConfig = field(default_factory=IntegrationConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    http_messaging: HttpMessagingConfig = field(default_factory=HttpMessagingConfig)
    phase_messaging: PhaseMessagingConfig = field(default_factory=PhaseMessagingConfig)
    edge_events: EdgeEventMessagingConfig = field(default_factory=EdgeEventMessagingConfig)
    inference_engine_class: str | None = field(default_factory=lambda: os.environ.get("INFERENCE_ENGINE_CLASS"))
    publish_engine_class: str | None = field(default_factory=lambda: os.environ.get("PUBLISH_ENGINE_CLASS"))
    streaming_engine_class: str | None = field(default_factory=lambda: os.environ.get("STREAMING_ENGINE_CLASS"))
    mode_server_enabled: bool = field(
        default_factory=lambda: _to_bool(os.environ.get("EDGE_MODE_SERVER_ENABLED"), False)
    )
    mode_server_host: str = field(default_factory=lambda: os.environ.get("EDGE_MODE_SERVER_HOST", "0.0.0.0"))
    mode_server_port: int = field(default_factory=lambda: int(os.environ.get("EDGE_MODE_SERVER_PORT", "9100")))
    poll_interval: float = field(default_factory=lambda: float(os.environ.get("EDGE_POLL_INTERVAL", "5")))
    retry_backoff: float = field(default_factory=lambda: float(os.environ.get("EDGE_RETRY_BACKOFF", "5")))
    monitor_endpoint: str | None = field(default_factory=lambda: os.environ.get("MONITOR_ENDPOINT"))
    monitor_service_name: str | None = field(
        default_factory=lambda: os.environ.get("EDGE_MONITOR_SERVICE_NAME") or os.environ.get("MONITOR_SERVICE_NAME")
    )

    def __post_init__(self) -> None:
        if not self.monitor_service_name:
            self.monitor_service_name = f"edge-{self.camera.camera_id}"

    @property
    def rtsp(self) -> RtspConfig:
        return self.ingestion.rtsp


def load_config() -> EdgeConfig:
    return EdgeConfig()
