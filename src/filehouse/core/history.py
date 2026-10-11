"""Back and forward history for one window."""


class History:
    def __init__(self):
        self._back = []
        self._forward = []
        self.current = None

    def visit(self, location):
        """Go somewhere new. This drops the forward history."""
        if self.current is not None:
            self._back.append(self.current)
        self.current = location
        self._forward.clear()

    @property
    def can_go_back(self):
        return bool(self._back)

    @property
    def can_go_forward(self):
        return bool(self._forward)

    def back(self):
        self._forward.append(self.current)
        self.current = self._back.pop()
        return self.current

    def forward(self):
        self._back.append(self.current)
        self.current = self._forward.pop()
        return self.current
