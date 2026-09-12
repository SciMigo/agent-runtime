"""Tests for event system."""

import pytest

from agent_runtime.events import (
    EventEmitter,
    EventType,
    RuntimeEvent,
    emit_event,
    event_emitter,
    subscribe,
)


class TestRuntimeEvent:
    """Tests for RuntimeEvent class."""

    def test_event_creation(self):
        """Test creating an event."""
        event = RuntimeEvent(
            event_type=EventType.CELL_STARTED,
            data={"code": "print('hello')"},
            lab_id="test-lab",
            cell_id="cell-1",
        )

        assert event.event_type == EventType.CELL_STARTED
        assert event.data == {"code": "print('hello')"}
        assert event.lab_id == "test-lab"
        assert event.cell_id == "cell-1"
        assert event.timestamp is not None
        assert event.event_id is not None

    def test_event_to_dict(self):
        """Test converting event to dictionary."""
        event = RuntimeEvent(
            event_type=EventType.CELL_COMPLETED,
            data={"success": True},
            lab_id="test-lab",
        )

        d = event.to_dict()

        assert d["event_type"] == "cell.completed"
        assert d["data"] == {"success": True}
        assert d["lab_id"] == "test-lab"
        assert "timestamp" in d
        assert "event_id" in d


class TestEventEmitter:
    """Tests for EventEmitter class."""

    @pytest.fixture
    def emitter(self):
        """Create a fresh event emitter."""
        return EventEmitter()

    def test_subscribe_and_emit(self, emitter):
        """Test subscribing to and emitting events."""
        received_events = []

        def handler(event: RuntimeEvent):
            received_events.append(event)

        emitter.subscribe(handler)
        emitter.emit(EventType.CELL_STARTED, {"code": "x = 1"})

        assert len(received_events) == 1
        assert received_events[0].event_type == EventType.CELL_STARTED

    def test_subscribe_multiple(self, emitter):
        """Test multiple subscribers receive events."""
        events1 = []
        events2 = []

        def handler1(event):
            events1.append(event)

        def handler2(event):
            events2.append(event)

        emitter.subscribe(handler1)
        emitter.subscribe(handler2)
        emitter.emit(EventType.CELL_STARTED, {})

        assert len(events1) == 1
        assert len(events2) == 1

    def test_unsubscribe(self, emitter):
        """Test unsubscribing from events."""
        received_events = []

        def handler(event):
            received_events.append(event)

        unsubscribe = emitter.subscribe(handler)
        emitter.emit(EventType.CELL_STARTED, {})
        assert len(received_events) == 1

        unsubscribe()
        emitter.emit(EventType.CELL_STARTED, {})
        assert len(received_events) == 1  # No new events

    def test_emit_with_context(self, emitter):
        """Test emitting events with lab/cell context."""
        received_events = []

        def handler(event: RuntimeEvent):
            received_events.append(event)

        emitter.subscribe(handler)
        emitter.emit(
            EventType.CELL_STREAM,
            {"text": "hello"},
            lab_id="my-lab",
            cell_id="my-cell",
        )

        assert len(received_events) == 1
        assert received_events[0].lab_id == "my-lab"
        assert received_events[0].cell_id == "my-cell"

    def test_event_history(self, emitter):
        """Test event history is maintained."""
        emitter.emit(EventType.KERNEL_STARTED, {"kernel": "k1"})
        emitter.emit(EventType.KERNEL_STARTED, {"kernel": "k2"})

        history = emitter.get_history()

        assert len(history) == 2

    def test_event_history_limit(self, emitter):
        """Test event history respects max limit."""
        # Emit more events than default limit
        for i in range(1100):
            emitter.emit(EventType.CELL_STARTED, {"i": i})

        history = emitter.get_history(limit=2000)

        # Internal limit is 1000
        assert len(history) <= 1000

    def test_filter_history_by_lab(self, emitter):
        """Test filtering history by lab ID."""
        emitter.emit(EventType.CELL_STARTED, {}, lab_id="lab1")
        emitter.emit(EventType.CELL_STARTED, {}, lab_id="lab2")
        emitter.emit(EventType.CELL_STARTED, {}, lab_id="lab1")

        history = emitter.get_history(lab_id="lab1")

        assert len(history) == 2
        assert all(e.lab_id == "lab1" for e in history)

    def test_filter_history_by_event_types(self, emitter):
        """Test filtering history by event types."""
        emitter.emit(EventType.CELL_STARTED, {})
        emitter.emit(EventType.CELL_COMPLETED, {})
        emitter.emit(EventType.CELL_STARTED, {})
        emitter.emit(EventType.KERNEL_STARTED, {})

        history = emitter.get_history(event_types=[EventType.CELL_STARTED])

        assert len(history) == 2
        assert all(e.event_type == EventType.CELL_STARTED for e in history)

    def test_clear_history(self, emitter):
        """Test clearing event history."""
        emitter.emit(EventType.CELL_STARTED, {})
        emitter.emit(EventType.CELL_COMPLETED, {})

        emitter.clear_history()

        assert len(emitter.get_history()) == 0

    def test_clear_history_by_lab(self, emitter):
        """Test clearing history for specific lab."""
        emitter.emit(EventType.CELL_STARTED, {}, lab_id="lab1")
        emitter.emit(EventType.CELL_STARTED, {}, lab_id="lab2")

        emitter.clear_history(lab_id="lab1")

        history = emitter.get_history()
        assert len(history) == 1
        assert history[0].lab_id == "lab2"

    @pytest.mark.asyncio
    async def test_subscribe_async(self, emitter):
        """Test async subscription."""
        received_events = []

        async def async_handler(event: RuntimeEvent):
            received_events.append(event)

        emitter.subscribe_async(async_handler)
        emitter.emit(EventType.CELL_STARTED, {"code": "x = 1"})

        # Give async task time to complete
        import asyncio

        await asyncio.sleep(0.1)

        assert len(received_events) == 1

    def test_subscriber_error_doesnt_break_emission(self, emitter):
        """Test that subscriber errors don't prevent other subscribers."""
        events = []

        def bad_handler(event):
            raise ValueError("Handler error")

        def good_handler(event):
            events.append(event)

        emitter.subscribe(bad_handler)
        emitter.subscribe(good_handler)

        # Should not raise
        emitter.emit(EventType.CELL_STARTED, {})

        # Good handler should still receive event
        assert len(events) == 1


class TestGlobalEventFunctions:
    """Tests for global event functions."""

    def test_emit_event(self):
        """Test global emit_event function."""
        # This should not raise
        event = emit_event(EventType.KERNEL_STARTED, {"kernel": "test"})
        assert event is not None
        assert event.event_type == EventType.KERNEL_STARTED

    def test_subscribe(self):
        """Test global subscribe function."""
        events = []

        def handler(event):
            events.append(event)

        unsubscribe = subscribe(handler)

        # Clear any existing history first
        event_emitter.clear_history()

        emit_event(EventType.CELL_STARTED, {"test": True})

        assert len(events) >= 1

        # Clean up
        unsubscribe()


class TestEventTypes:
    """Tests for EventType enum."""

    def test_cell_events(self):
        """Test cell-related event types."""
        assert EventType.CELL_STARTED.value == "cell.started"
        assert EventType.CELL_COMPLETED.value == "cell.completed"
        assert EventType.CELL_STREAM.value == "cell.stream"
        assert EventType.CELL_ERROR.value == "cell.error"
        assert EventType.CELL_RESULT.value == "cell.result"

    def test_kernel_events(self):
        """Test kernel-related event types."""
        assert EventType.KERNEL_STARTING.value == "kernel.starting"
        assert EventType.KERNEL_STARTED.value == "kernel.started"
        assert EventType.KERNEL_RESTARTING.value == "kernel.restarting"
        assert EventType.KERNEL_INTERRUPTED.value == "kernel.interrupted"
        assert EventType.KERNEL_SHUTDOWN.value == "kernel.shutdown"
        assert EventType.KERNEL_BUSY.value == "kernel.busy"
        assert EventType.KERNEL_IDLE.value == "kernel.idle"

    def test_env_events(self):
        """Test environment-related event types."""
        assert EventType.ENV_CREATING.value == "env.creating"
        assert EventType.ENV_CREATED.value == "env.created"
        assert EventType.ENV_DELETED.value == "env.deleted"
        assert EventType.PACKAGES_INSTALLING.value == "packages.installing"
        assert EventType.PACKAGES_INSTALLED.value == "packages.installed"
        assert EventType.PACKAGES_FAILED.value == "packages.failed"

    def test_auth_events(self):
        """Test authentication-related event types."""
        assert EventType.PAIRING_REQUESTED.value == "pairing.requested"
        assert EventType.PAIRING_APPROVED.value == "pairing.approved"
        assert EventType.PAIRING_REJECTED.value == "pairing.rejected"

    def test_error_event(self):
        """Test error event type."""
        assert EventType.ERROR.value == "error"
