"""Base client interface for storage backends."""

from abc import ABC, abstractmethod

class StorageClient(ABC):
    """Abstract base class for all storage clients.
    
    Operates purely on URIs and local paths, without knowledge of application logic.
    """

    @abstractmethod
    def ls(self, uri: str) -> list[str]:
        """Lists object names under the given URI."""
        pass

    @abstractmethod
    def push(self, uri: str, local_dir: str) -> int:
        """Pushes a local directory to the remote URI. Returns count of uploaded files."""
        pass

    @abstractmethod
    def exists(self, uri: str) -> bool:
        """Checks if the URI exists."""
        pass

    @abstractmethod
    def pull(self, uri: str, dest_dir: str) -> int:
        """Pulls objects from URI to dest_dir. Returns count of downloaded files."""
        pass
