"""Request-scoped event sink; list remains usable in standalone processor tests."""
from contextvars import ContextVar

event_sink = ContextVar('travel_event_sink', default=None)


class StageEvents(list):
    def append(self, event):
        super().append(event)
        sink = event_sink.get()
        if sink:
            sink(event)

    def extend(self, events):
        for event in events:
            self.append(event)
