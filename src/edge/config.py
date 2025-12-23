"""Edge 模組設定讀取。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


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
class EdgeConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    visualization: VisualizationConfig = field(default_factory=VisualizationConfig)
    ingestion: IngestionConfig = field(default_factory=IngestionConfig)
    integration: IntegrationConfig = field(default_factory=IntegrationConfig)
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
