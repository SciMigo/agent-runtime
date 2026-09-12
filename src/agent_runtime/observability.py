"""Observability module for Agent Runtime.

Provides:
- Structured logging
- Span/trace emission (for future gateway integration)
- Metrics collection hooks
"""

import json
import logging
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from agent_runtime.events import EventType, event_emitter


# Configure structured logging
class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields
        if hasattr(record, "lab_id"):
            log_data["lab_id"] = record.lab_id
        if hasattr(record, "cell_id"):
            log_data["cell_id"] = record.cell_id
        if hasattr(record, "span_id"):
            log_data["span_id"] = record.span_id

        # Add exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def setup_logging(structured: bool = False, level: int = logging.INFO) -> None:
    """Configure logging for the runtime."""
    logger = logging.getLogger("agent_runtime")
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)

    if structured:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    logger.addHandler(handler)


# Get logger
logger = logging.getLogger("agent_runtime")


@dataclass
class Span:
    """A trace span for observability."""

    name: str
    span_id: str = field(default_factory=lambda: str(uuid4())[:8])
    parent_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    error: str | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add an event to the span."""
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            }
        )

    def set_error(self, error: str) -> None:
        """Mark span as errored."""
        self.status = "error"
        self.error = error

    def end(self) -> None:
        """End the span."""
        self.end_time = time.time()

    @property
    def duration_ms(self) -> float | None:
        """Get span duration in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for export."""
        return {
            "name": self.name,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status,
            "error": self.error,
        }


class SpanExporter:
    """Base class for span exporters."""

    def export(self, span: Span) -> None:
        """Export a completed span."""
        pass


class ConsoleSpanExporter(SpanExporter):
    """Export spans to console (for debugging)."""

    def export(self, span: Span) -> None:
        logger.debug(f"Span completed: {json.dumps(span.to_dict())}")


class EventSpanExporter(SpanExporter):
    """Export spans as runtime events (for gateway integration)."""

    def export(self, span: Span) -> None:
        # This could be picked up by the gateway for trace aggregation
        event_emitter.emit(
            EventType.CELL_COMPLETED if "cell" in span.name else EventType.KERNEL_IDLE,
            {"span": span.to_dict()},
            lab_id=span.attributes.get("lab_id"),
            cell_id=span.attributes.get("cell_id"),
        )


# Global span exporter
_span_exporter: SpanExporter = ConsoleSpanExporter()


def set_span_exporter(exporter: SpanExporter) -> None:
    """Set the global span exporter."""
    global _span_exporter
    _span_exporter = exporter


@contextmanager
def trace_span(
    name: str,
    lab_id: str | None = None,
    cell_id: str | None = None,
    parent_id: str | None = None,
    **attributes: Any,
) -> Generator[Span, None, None]:
    """Context manager for creating traced spans.

    Usage:
        with trace_span("cell_execution", lab_id="123", cell_id="abc") as span:
            span.set_attribute("code_length", len(code))
            result = execute_code(code)
            span.add_event("execution_complete")
    """
    span = Span(name=name, parent_id=parent_id)
    span.attributes.update(attributes)

    if lab_id:
        span.set_attribute("lab_id", lab_id)
    if cell_id:
        span.set_attribute("cell_id", cell_id)

    try:
        yield span
    except Exception as e:
        span.set_error(str(e))
        raise
    finally:
        span.end()
        _span_exporter.export(span)


# Metrics collection (simple counters for now)
class Metrics:
    """Simple metrics collection."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

    def increment(self, name: str, value: int = 1, tags: dict[str, str] | None = None) -> None:
        """Increment a counter."""
        key = self._make_key(name, tags)
        self._counters[key] = self._counters.get(key, 0) + value

    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Set a gauge value."""
        key = self._make_key(name, tags)
        self._gauges[key] = value

    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record a histogram value."""
        key = self._make_key(name, tags)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)

    def _make_key(self, name: str, tags: dict[str, str] | None) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}[{tag_str}]"

    def get_all(self) -> dict[str, Any]:
        """Get all metrics."""
        return {
            "counters": self._counters.copy(),
            "gauges": self._gauges.copy(),
            "histograms": {
                k: {
                    "count": len(v),
                    "sum": sum(v),
                    "min": min(v) if v else 0,
                    "max": max(v) if v else 0,
                    "avg": sum(v) / len(v) if v else 0,
                }
                for k, v in self._histograms.items()
            },
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()


# Global metrics instance
metrics = Metrics()
