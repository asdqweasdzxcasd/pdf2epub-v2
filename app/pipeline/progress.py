"""변환 진행률 콜백"""

import sys
from dataclasses import dataclass, field
from typing import Protocol


class ProgressCallback(Protocol):
    """진행률 콜백 프로토콜. Week 2에서 DB 업데이트 구현체 추가 예정."""

    def update(self, progress: int, stage: str, message: str = "") -> None: ...


@dataclass
class CliProgress:
    """CLI용 진행률 출력. stderr로 진행 상황을 표시한다."""

    _last_progress: int = field(default=-1, init=False)

    def update(self, progress: int, stage: str, message: str = "") -> None:
        # 동일 진행률 중복 출력 방지
        if progress != self._last_progress:
            self._last_progress = progress
            msg = f"[{progress:3d}%] {stage}"
            if message:
                msg += f" - {message}"
            print(msg, file=sys.stderr)
