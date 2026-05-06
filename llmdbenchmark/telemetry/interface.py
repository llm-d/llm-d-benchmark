from abc import ABC, abstractmethod

class TelemetryProvider(ABC):
    """Abstract base class for telemetry providers."""

    @abstractmethod
    def push(self, data: dict) -> None:
        """Push telemetry data.

        Args:
            data: A dictionary containing telemetry fields.
        """
        pass
