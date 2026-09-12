"""Event system for Agent Runtime.

Provides structured events for:
- Execution outputs (stdout, stderr, results)
- Kernel lifecycle events
- Environment events
- Error events
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class EventType(StrEnum):
    """Types of events emitted by the runtime."""

    # Execution events
    CELL_STARTED = "cell.started"
    CELL_STREAM = "cell.stream"  # stdout/stderr
    CELL_RESULT = "cell.result"
    CELL_ERROR = "cell.error"
    CELL_COMPLETED = "cell.completed"

    # Kernel events
    KERNEL_STARTING = "kernel.starting"
    KERNEL_STARTED = "kernel.started"
    KERNEL_BUSY = "kernel.busy"
    KERNEL_IDLE = "kernel.idle"
    KERNEL_INTERRUPTED = "kernel.interrupted"
    KERNEL_RESTARTING = "kernel.restarting"
    KERNEL_SHUTDOWN = "kernel.shutdown"

    # Kernel spec events
    KERNEL_SPEC_INSTALLING = "kernel_spec.installing"
    KERNEL_SPEC_INSTALLED = "kernel_spec.installed"

    # Environment events
    ENV_CREATING = "env.creating"
    ENV_CREATED = "env.created"
    ENV_DELETED = "env.deleted"
    PACKAGES_INSTALLING = "packages.installing"
    PACKAGES_INSTALLED = "packages.installed"
    PACKAGES_FAILED = "packages.failed"

    # Auth events
    PAIRING_REQUESTED = "pairing.requested"
    PAIRING_APPROVED = "pairing.approved"
    PAIRING_REJECTED = "pairing.rejected"

    # Error events
    ERROR = "error"


@dataclass
class RuntimeEvent:
    """A structured event from the runtime."""

    event_type: EventType
    data: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    lab_id: str | None = None
    cell_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "lab_id": self.lab_id,
            "cell_id": self.cell_id,
            "data": self.data,
        }


class EventEmitter:
    """Manages event emission and subscriptions."""

    def __init__(self) -> None:
        self._subscribers: list[Callable[[RuntimeEvent], None]] = []
        self._async_subscribers: list[Callable[[RuntimeEvent], Any]] = []
        self._event_history: list[RuntimeEvent] = []
        self._max_history: int = 1000

    def subscribe(self, callback: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
        """Subscribe to events. Returns an unsubscribe function."""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def subscribe_async(self, callback: Callable[[RuntimeEvent], Any]) -> Callable[[], None]:
        """Subscribe to events with async callback. Returns an unsubscribe function."""
        self._async_subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._async_subscribers:
                self._async_subscribers.remove(callback)

        return unsubscribe

    def emit(
        self,
        event_type: EventType,
        data: dict[str, Any],
        lab_id: str | None = None,
        cell_id: str | None = None,
    ) -> RuntimeEvent:
        """Emit an event to all subscribers."""
        event = RuntimeEvent(
            event_type=event_type,
            data=data,
            lab_id=lab_id,
            cell_id=cell_id,
        )

        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history :]

        # Notify sync subscribers
        for callback in self._subscribers:
            try:
                callback(event)
            except Exception:
                pass  # Don't let subscriber errors break emission

        # Notify async subscribers
        for callback in self._async_subscribers:
            try:
                result = callback(event)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                pass

        return event

    def get_history(
        self,
        lab_id: str | None = None,
        event_types: list[EventType] | None = None,
        limit: int = 100,
    ) -> list[RuntimeEvent]:
        """Get recent events, optionally filtered."""
        events = self._event_history

        if lab_id:
            events = [e for e in events if e.lab_id == lab_id]

        if event_types:
            events = [e for e in events if e.event_type in event_types]

        return events[-limit:]

    def clear_history(self, lab_id: str | None = None) -> None:
        """Clear event history."""
        if lab_id:
            self._event_history = [e for e in self._event_history if e.lab_id != lab_id]
        else:
            self._event_history = []


# Global event emitter
event_emitter = EventEmitter()


def emit_event(
    event_type: EventType,
    data: dict[str, Any],
    lab_id: str | None = None,
    cell_id: str | None = None,
) -> RuntimeEvent:
    """Convenience function to emit an event."""
    return event_emitter.emit(event_type, data, lab_id, cell_id)


def subscribe(callback: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
    """Convenience function to subscribe to events."""
    return event_emitter.subscribe(callback)
