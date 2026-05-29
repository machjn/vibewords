from abc import ABC, abstractmethod


class Connector(ABC):
    """Base class for all puzzle connectors."""

    @property
    @abstractmethod
    def connector_id(self) -> str:
        """Unique key, e.g. 'guardian_cryptic', 'local'."""

    @property
    @abstractmethod
    def source(self) -> str:
        """Source group for config gating and UI grouping, e.g. 'guardian'."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Display name for the source, e.g. 'Guardian'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Display name for this connector/type, e.g. 'Cryptic'."""

    @property
    def schedule(self) -> str | None:
        """Optional publication schedule hint, e.g. 'Mon–Sat'. Override if applicable."""
        return None
