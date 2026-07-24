from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _install_smart_messaging_core_stub() -> None:
    if "smart_messaging_core" in sys.modules:
        return
    try:
        __import__("smart_messaging_core")
        return
    except ModuleNotFoundError:
        pass

    module = ModuleType("smart_messaging_core")

    @dataclass
    class HttpConfig:  # pragma: no cover - simple import stub
        base_url: str | None = None
        timeout_seconds: int | None = None
        listen_host: str | None = None
        listen_port: int | None = None

    @dataclass
    class MqttConfig:  # pragma: no cover - simple import stub
        host: str | None = None
        port: int | None = None
        qos: int | None = None
        retain: bool | None = None
        client_id: str | None = None
        auth_enabled: bool | None = None
        username: str | None = None
        password: str | None = None

    @dataclass
    class MessagingConfig:  # pragma: no cover - simple import stub
        mqtt: object | None = None
        http: object | None = None
        routes: dict[str, object] | None = None

    @dataclass
    class RouteConfig:  # pragma: no cover - simple import stub
        backend: str
        channel: str

    class MessagingClient:  # pragma: no cover - simple import stub
        def __init__(self, config: MessagingConfig | None = None) -> None:
            self.config = config
            self.subscriptions: dict[str, object] = {}
            self.closed = False

        def subscribe(self, route_key: str, callback):
            self.subscriptions[route_key] = callback

        def close(self) -> None:
            self.closed = True

    module.HttpConfig = HttpConfig
    module.MqttConfig = MqttConfig
    module.MessagingConfig = MessagingConfig
    module.RouteConfig = RouteConfig
    module.MessagingClient = MessagingClient
    sys.modules["smart_messaging_core"] = module


def _install_smart_workflow_stub() -> None:
    if "smart_workflow" in sys.modules:
        return
    try:
        __import__("smart_workflow")
        return
    except ModuleNotFoundError:
        pass

    module = ModuleType("smart_workflow")

    class TaskError(Exception):
        pass

    @dataclass
    class TaskResult:  # pragma: no cover - simple import stub
        payload: object | None = None

    class BaseTask:  # pragma: no cover - simple import stub
        name = "stub-task"

        def execute(self, context):
            return self.run(context)

        def run(self, context):
            raise NotImplementedError

    class TaskContext:  # pragma: no cover - simple import stub
        def __init__(self, logger=None, config=None, monitor=None, *args, **kwargs) -> None:
            self.logger = logger
            self.config = config
            self.monitor = monitor
            self._resources: dict[str, object] = {}
            self.args = args
            self.kwargs = kwargs

        def set_resource(self, key: str, value) -> None:
            self._resources[key] = value

        def get_resource(self, key: str, default=None):
            return self._resources.get(key, default)

        def require_resource(self, key: str):
            if key not in self._resources:
                raise KeyError(key)
            return self._resources[key]

    class MonitoringClient:  # pragma: no cover - simple import stub
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class WorkflowRunner:  # pragma: no cover - simple import stub
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        def run(self) -> None:
            return None

    class HealthAwareWorkflowRunner(WorkflowRunner):  # pragma: no cover - simple import stub
        pass

    module.TaskError = TaskError
    module.TaskResult = TaskResult
    module.BaseTask = BaseTask
    module.TaskContext = TaskContext
    module.MonitoringClient = MonitoringClient
    module.WorkflowRunner = WorkflowRunner
    module.HealthAwareWorkflowRunner = HealthAwareWorkflowRunner
    sys.modules["smart_workflow"] = module


_install_smart_messaging_core_stub()
_install_smart_workflow_stub()
