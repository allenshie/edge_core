"""Edge 模組設定讀取。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _to_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.lower() not in {"0", "false", "no"}


@dataclass
class CameraConfig:
    camera_id: str = os.environ.get("EDGE_CAMERA_ID", "cam01")


@dataclass
class ModelConfig:
    name: str = os.environ.get("EDGE_MODEL_NAME", "yolo11n")
    weights_path: str = os.environ.get("EDGE_MODEL_PATH", "./A6_PN001_20250806_yolov12s-dec_v1.pt")
    confidence_threshold: float = float(os.environ.get("EDGE_CONF_THRESHOLD", "0.5"))
    device: str | None = os.environ.get("EDGE_MODEL_DEVICE")
    visualize: bool = _to_bool(os.environ.get("EDGE_MODEL_VISUALIZE"), True)
    tracker_config: str | None = os.environ.get("EDGE_TRACKER_CONFIG", "trackers/bytetrack.yaml")

    def resolve_tracker_config(self, project_root: Path | None = None) -> str | None:
        """Return tracker config path or name.

        - If user指定為空則回傳 None，表示使用 Ultralytics 預設（ByteTrack）。
        - 若為絕對路徑或 repo 相對路徑且檔案存在，回傳實際檔案位置，
          以支援自訂 tracker 設定。
        - 其他情況（如 `botsort.yaml`）直接回傳文字，讓 Ultralytics 載入內建 cfg。
        """

        cfg = (self.tracker_config or "").strip()
        if not cfg:
            return None

        cfg_path = Path(cfg)
        if cfg_path.is_absolute():
            if not cfg_path.exists():
                raise FileNotFoundError(f"找不到指定的 tracker config: {cfg_path}")
            return str(cfg_path)

        search_root = project_root or Path(__file__).resolve().parents[2]
        candidate = (search_root / cfg_path).resolve()
        if candidate.exists():
            return str(candidate)

        # 若 path 含子資料夾，視為自訂檔案但不存在，明確丟出錯誤避免 fallback 到內建配置
        if len(cfg_path.parts) > 1 or cfg_path.parts[0] in {".", ".."}:
            raise FileNotFoundError(f"找不到相對於 {search_root} 的 tracker config: {cfg}")

        # 單純檔名則交給 Ultralytics 解析（botsort.yaml / bytetrack.yaml 等）
        return cfg


@dataclass
class VisualizationConfig:
    enabled: bool = _to_bool(
        os.environ.get("EDGE_VISUAL_ENABLED"),
        _to_bool(os.environ.get("EDGE_MODEL_VISUALIZE"), True),
    )
    mode: str = os.environ.get("EDGE_VISUAL_MODE", "write").strip().lower()
    window_name: str = os.environ.get("EDGE_VISUAL_WINDOW", "edge-preview")
    window_width: int = int(os.environ.get("EDGE_VISUAL_WIDTH", "1280"))
    window_height: int = int(os.environ.get("EDGE_VISUAL_HEIGHT", "720"))


@dataclass
class RtspConfig:
    url: str = os.environ.get("EDGE_RTSP_URL", "rtsp://localhost:554/stream")
    drop_frames: int = int(os.environ.get("EDGE_RTSP_DROP_FRAMES", "2"))
    reconnect_seconds: float = float(os.environ.get("EDGE_RTSP_RECONNECT", "1"))
    fps: float = float(os.environ.get("EDGE_RTSP_FPS", "30"))
    frame_width: int | None = (
        int(os.environ.get("EDGE_RTSP_WIDTH")) if os.environ.get("EDGE_RTSP_WIDTH") else None
    )
    frame_height: int | None = (
        int(os.environ.get("EDGE_RTSP_HEIGHT")) if os.environ.get("EDGE_RTSP_HEIGHT") else None
    )


@dataclass
class IntegrationConfig:
    api_base: str = os.environ.get("INTEGRATION_API_BASE", "http://localhost:9000")
    timeout_seconds: int = int(os.environ.get("INTEGRATION_API_TIMEOUT", "5"))


@dataclass
class MqttConfig:
    enabled: bool = _to_bool(os.environ.get("EDGE_MQTT_ENABLED"), False)
    host: str = os.environ.get("EDGE_MQTT_HOST", "localhost")
    port: int = int(os.environ.get("EDGE_MQTT_PORT", "1883"))
    topic: str = os.environ.get("EDGE_PHASE_MQTT_TOPIC", "integration/phase")
    qos: int = int(os.environ.get("EDGE_MQTT_QOS", "1"))
    client_id: str | None = os.environ.get("EDGE_MQTT_CLIENT_ID")
    auth_enabled: bool = _to_bool(os.environ.get("EDGE_MQTT_AUTH_ENABLED"), False)
    username: str | None = os.environ.get("EDGE_MQTT_USERNAME")
    password: str | None = os.environ.get("EDGE_MQTT_PASSWORD")


@dataclass
class FileSourceConfig:
    path: str | None = os.environ.get("EDGE_FILE_PATH")
    loop: bool = _to_bool(os.environ.get("EDGE_FILE_LOOP"), True)
    drop_frames: int = int(
        os.environ.get("EDGE_FILE_DROP_FRAMES", os.environ.get("EDGE_RTSP_DROP_FRAMES", "0"))
    )
    fps: float | None = float(os.environ["EDGE_FILE_FPS"]) if os.environ.get("EDGE_FILE_FPS") else None


@dataclass
class IngestionConfig:
    mode: str = os.environ.get("EDGE_INGEST_MODE", "rtsp")
    rtsp: RtspConfig = field(default_factory=RtspConfig)
    file: FileSourceConfig = field(default_factory=FileSourceConfig)

    def __post_init__(self) -> None:
        self.mode = (self.mode or "rtsp").strip().lower()


@dataclass
class StreamingConfig:
    enabled: bool = _to_bool(os.environ.get("EDGE_STREAMING_ENABLED"), False)
    strategy: str = os.environ.get("EDGE_STREAMING_STRATEGY", "cpu").strip().lower()
    url: str = os.environ.get("EDGE_STREAMING_URL", "").strip()
    queue_size: int = int(os.environ.get("EDGE_STREAMING_QUEUE_SIZE", "30"))
    idle_timeout_seconds: float = float(os.environ.get("EDGE_STREAMING_IDLE_TIMEOUT", "3"))
    restart_backoff_seconds: float = float(os.environ.get("EDGE_STREAMING_RESTART_BACKOFF", "1"))


@dataclass
class EdgeConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    visualization: VisualizationConfig = field(default_factory=VisualizationConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    integration: IntegrationConfig = field(default_factory=IntegrationConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    inference_engine_class: str | None = os.environ.get("INFERENCE_ENGINE_CLASS")
    publish_engine_class: str | None = os.environ.get("PUBLISH_ENGINE_CLASS")
    mode_server_enabled: bool = _to_bool(os.environ.get("EDGE_MODE_SERVER_ENABLED"), False)
    mode_server_host: str = os.environ.get("EDGE_MODE_SERVER_HOST", "0.0.0.0")
    mode_server_port: int = int(os.environ.get("EDGE_MODE_SERVER_PORT", "9100"))
    poll_interval: float = float(os.environ.get("EDGE_POLL_INTERVAL", "5"))
    retry_backoff: float = float(os.environ.get("EDGE_RETRY_BACKOFF", "5"))
    monitor_endpoint: str | None = os.environ.get("MONITOR_ENDPOINT")
    monitor_service_name: str | None = os.environ.get("EDGE_MONITOR_SERVICE_NAME") or os.environ.get(
        "MONITOR_SERVICE_NAME"
    )

    def __post_init__(self) -> None:
        if not self.monitor_service_name:
            self.monitor_service_name = f"edge-{self.camera.camera_id}"

    @property
    def rtsp(self) -> RtspConfig:
        """Backward compatibility for code still accessing config.rtsp directly."""
        return self.ingestion.rtsp


def load_config() -> EdgeConfig:
    return EdgeConfig()
