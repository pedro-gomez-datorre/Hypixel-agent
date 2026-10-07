"""Discord notifications. Never raises: a dead webhook must not kill the agent."""
import logging
import time

import requests

log = logging.getLogger(__name__)


class Discord:
    def __init__(self, webhook: str, cooldown: float):
        self.webhook, self.cooldown = webhook, cooldown
        self._last: dict[str, float] = {}

    def send(self, key: str, text: str) -> bool:
        """Send ``text`` unless disabled or ``key`` was sent within the cooldown."""
        if not self.webhook:
            return False
        now = time.time()
        if now - self._last.get(key, 0) < self.cooldown:
            return False
        try:
            requests.post(self.webhook, json={"content": text}, timeout=10).raise_for_status()
        except requests.RequestException as e:
            log.warning("discord notification failed: %s", e)
            return False
        self._last[key] = now
        return True
