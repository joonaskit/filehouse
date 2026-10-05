"""Navigation history manager (back / forward navigation stack)."""

from pathlib import Path


class NavigationHistory:
    """Maintains back and forward navigation stacks for file browsing."""

    def __init__(self, initial_path: Path | None = None):
        self._back_stack: list[Path] = []
        self._forward_stack: list[Path] = []
        self._current_path: Path = initial_path or Path.home()

    @property
    def current(self) -> Path:
        return self._current_path

    @property
    def can_go_back(self) -> bool:
        return len(self._back_stack) > 0

    @property
    def can_go_forward(self) -> bool:
        return len(self._forward_stack) > 0

    def navigate_to(self, path: Path | str) -> Path:
        target = Path(path).resolve()
        if target != self._current_path:
            self._back_stack.append(self._current_path)
            self._forward_stack.clear()
            self._current_path = target
        return self._current_path

    def back(self) -> Path | None:
        if not self.can_go_back:
            return None
        self._forward_stack.append(self._current_path)
        self._current_path = self._back_stack.pop()
        return self._current_path

    def forward(self) -> Path | None:
        if not self.can_go_forward:
            return None
        self._back_stack.append(self._current_path)
        self._current_path = self._forward_stack.pop()
        return self._current_path
