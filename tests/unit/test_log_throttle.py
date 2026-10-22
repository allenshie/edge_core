from __future__ import annotations

import logging

from edge.runtime.log_throttle import RateLimitedLogFilter, install_mqtt_log_throttle


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _record(msg: str, *args, name: str = "smart_messaging_core.protocols.mqtt", level: int = logging.WARNING):
    return logging.LogRecord(name, level, __file__, 1, msg, args, None)


def test_only_first_record_passes_within_window() -> None:
    clock = _FakeClock()
    log_filter = RateLimitedLogFilter(window_seconds=60.0, clock=clock)

    assert log_filter.filter(_record("MQTT publish failed (rc=%s)", 4)) is True
    clock.now = 10.0
    assert log_filter.filter(_record("MQTT publish failed (rc=%s)", 4)) is False
    clock.now = 59.0
    assert log_filter.filter(_record("MQTT publish failed (rc=%s)", 7)) is False


def test_record_after_window_reports_suppressed_count() -> None:
    clock = _FakeClock()
    log_filter = RateLimitedLogFilter(window_seconds=60.0, clock=clock)
    log_filter.filter(_record("MQTT publish failed (rc=%s)", 4))
    for _ in range(3):
        log_filter.filter(_record("MQTT publish failed (rc=%s)", 4))

    clock.now = 60.0
    record = _record("MQTT publish failed (rc=%s)", 4)
    assert log_filter.filter(record) is True
    assert record.getMessage() == "MQTT publish failed (rc=4)（過去 60s 略過 3 筆）"

    clock.now = 61.0
    assert log_filter.filter(_record("MQTT publish failed (rc=%s)", 4)) is False
    clock.now = 120.0
    record = _record("MQTT publish failed (rc=%s)", 4)
    assert log_filter.filter(record) is True
    assert record.getMessage() == "MQTT publish failed (rc=4)（過去 60s 略過 1 筆）"


def test_record_after_quiet_window_is_unchanged() -> None:
    clock = _FakeClock()
    log_filter = RateLimitedLogFilter(window_seconds=60.0, clock=clock)
    log_filter.filter(_record("MQTT publish failed (rc=%s)", 4))

    clock.now = 100.0
    record = _record("MQTT publish failed (rc=%s)", 4)
    assert log_filter.filter(record) is True
    assert record.getMessage() == "MQTT publish failed (rc=4)"


def test_different_templates_and_levels_are_independent() -> None:
    clock = _FakeClock()
    log_filter = RateLimitedLogFilter(window_seconds=60.0, clock=clock)

    assert log_filter.filter(_record("MQTT publish failed (rc=%s)", 4)) is True
    assert log_filter.filter(_record("MQTT connect failed (rc=%s)", 5)) is True
    assert log_filter.filter(_record("MQTT publish failed (rc=%s)", 4, level=logging.ERROR)) is True


def test_zero_window_disables_throttle() -> None:
    log_filter = RateLimitedLogFilter(window_seconds=0.0, clock=_FakeClock())

    assert all(log_filter.filter(_record("MQTT publish failed (rc=%s)", 4)) for _ in range(5))


def test_install_attaches_filter_to_mqtt_logger_only(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_MQTT_LOG_THROTTLE_SECONDS", "30")
    mqtt_logger = logging.getLogger("smart_messaging_core.protocols.mqtt")
    before = list(mqtt_logger.filters)
    try:
        log_filter = install_mqtt_log_throttle()
        assert log_filter is not None
        assert log_filter.window_seconds == 30.0
        assert log_filter in mqtt_logger.filters
        assert log_filter not in logging.getLogger("edge").filters
        # 重複呼叫不會掛上第二個 filter。
        assert install_mqtt_log_throttle() is log_filter
        assert sum(isinstance(f, RateLimitedLogFilter) for f in mqtt_logger.filters) == 1
    finally:
        mqtt_logger.filters = before


def test_install_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_MQTT_LOG_THROTTLE_SECONDS", "0")
    mqtt_logger = logging.getLogger("smart_messaging_core.protocols.mqtt")
    before = list(mqtt_logger.filters)
    try:
        assert install_mqtt_log_throttle() is None
        assert mqtt_logger.filters == before
    finally:
        mqtt_logger.filters = before
