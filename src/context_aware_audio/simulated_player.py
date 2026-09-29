"""
simulated_player.py - مشغل صوتي وهمي (للمحاكاة والاختبارات فقط)
لا يُخرج صوتاً حقيقياً: يتتبع الملف والحجم ويطبع وصفاً نصياً.
للتشغيل الحقيقي استخدم real_player.RealPlayer.
"""

import time
from typing import Optional

from .audio_types import PlaybackCommand


class SimulatedPlayer:
    """يحاكي مشغلاً صوتياً: يتتبع الملف الحالي والحجم مع Fade تدريجي."""

    def __init__(self):
        self.current_file: Optional[str] = None
        self.current_volume: float = 1.0
        self.current_db: float = 0.0
        self.is_muted: bool = True
        self.history: list = []

    def apply(self, cmd: PlaybackCommand) -> str:
        """يطبّق أمراً ويعيد وصفاً نصياً (يحاكي Fade دون انتظار حقيقي)."""
        prev = self.current_file
        if cmd.is_muted or cmd.file is None:
            action = (
                f"🔇 MUTE (fade {cmd.fade_duration_sec}s)"
                if not self.is_muted
                else "🔇 يبقى صامتاً"
            )
            self.is_muted = True
            self.current_volume = 0.0
        else:
            if prev != cmd.file:
                action = f"🎵 {prev or '—'} → {cmd.file} (crossfade {cmd.fade_duration_sec}s)"
            else:
                action = f"🎵 {cmd.file} vol {self.current_volume:.0%} → {cmd.volume_ratio:.0%}"
            self.current_file = cmd.file
            self.current_volume = cmd.volume_ratio
            self.current_db = cmd.target_db
            self.is_muted = False

        line = f"{action} | {cmd.state.value} | {cmd.reason}"
        self.history.append((time.time(), str(cmd), line))
        return line
