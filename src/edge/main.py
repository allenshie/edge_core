from __future__ import annotations

import logging
import os
import sys

from smart_workflow import (
    HealthAwareWorkflowRunner,
    MonitoringClient,
    TaskContext,
    WorkflowRunner,
)

from edge.api.mode_server import MODE_RESOURCE, start_mode_server, stop_mode_server
from edge.config import EdgeConfig, load_config
from edge.pipeline import build_edge_workflow
from edge.runtime.health_runtime import start_health_server, stop_health_server
from edge.runtime.messaging_runtime import close_messaging_client, init_messaging_client, start_messaging_subscriber
from edge.runtime.shutdown_summary import (
    append_shutdown_records,
    emit_shutdown_summary,
    get_shutdown_records,
    normalize_cleanup_records,
)


def setup_logging() -> None:
    level_name = os.environ.get("EDGE_LOG_LEVEL") or os.environ.get("LOG_LEVEL") or "INFO"
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )


def build_context(config: EdgeConfig) -> TaskContext:
    logger = logging.getLogger("edge")
    monitor = MonitoringClient(
        config.monitor_endpoint,
        service_name=config.monitor_service_name,
    )
    context = TaskContext(
        logger=logger,
        config=config,
        monitor=monitor,
    )
    default_mode = os.environ.get("EDGE_MODE_DEFAULT")
    context.set_resource(MODE_RESOURCE, default_mode)
    return context


def run_daemon(config: EdgeConfig) -> None:
    context = build_context(config)
    logger = context.logger

    init_messaging_client(context, logger)

    mode_server = None
    if config.mode_server_enabled:
        mode_server = start_mode_server(config.mode_server_host, config.mode_server_port, context)
    start_messaging_subscriber(context)

    workflow = build_edge_workflow()
    health_server, health_state = start_health_server(context, logger)

    if health_state is not None:
        runner = HealthAwareWorkflowRunner(
            context=context,
            workflow=workflow,
            loop_interval=config.poll_interval,
            retry_backoff=config.retry_backoff,
            health_state=health_state,
        )
    else:
        runner = WorkflowRunner(
            context=context,
            workflow=workflow,
            loop_interval=config.poll_interval,
            retry_backoff=config.retry_backoff,
        )

    try:
        runner.run()
    finally:
        logger.info("shutting down")
        shutdown_records: list[dict[str, object]] = []
        pipeline = context.get_resource("edge_pipeline")
        if pipeline is not None:
            close_fn = getattr(pipeline, "close", None)
            if callable(close_fn):
                shutdown_records.extend(normalize_cleanup_records(close_fn(context), fallback_type="task"))

        shutdown_records.extend(append_shutdown_records(context, stop_mode_server(mode_server)))
        shutdown_records.extend(append_shutdown_records(context, close_messaging_client(context)))
        shutdown_records.extend(append_shutdown_records(context, stop_health_server(health_server)))

        context_records = get_shutdown_records(context)
        if context_records:
            shutdown_records.extend(
                record for record in context_records if record not in shutdown_records
            )
        emit_shutdown_summary(logger, shutdown_records)


def main() -> None:
    setup_logging()
    config = load_config()
    run_daemon(config)


if __name__ == "__main__":
    main()
