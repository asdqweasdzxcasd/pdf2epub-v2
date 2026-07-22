"""DEVICE 환경변수 해석 및 ORT provider 선택"""

from dataclasses import dataclass
from enum import Enum

from app.config import settings


class Device(str, Enum):
    CPU = "cpu"
    CUDA = "cuda"
    AUTO = "auto"


@dataclass(frozen=True)
class RuntimeProfile:
    device: Device
    ort_providers: list[str]
    torch_device: str  # "cpu" or "cuda:0"
    thread_count: int


def detect() -> RuntimeProfile:
    """현재 환경에 맞는 RuntimeProfile을 반환한다."""
    want = Device(settings.DEVICE)
    if want is Device.AUTO:
        want = _probe()
    if want is Device.CUDA:
        return RuntimeProfile(
            device=Device.CUDA,
            ort_providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            torch_device="cuda:0",
            thread_count=4,
        )
    return RuntimeProfile(
        device=Device.CPU,
        ort_providers=["CPUExecutionProvider"],
        torch_device="cpu",
        thread_count=settings.OMP_NUM_THREADS,
    )


def _probe() -> Device:
    """GPU 사용 가능 여부를 자동 감지한다."""
    try:
        import torch

        if torch.cuda.is_available():
            return Device.CUDA
    except Exception:
        pass
    return Device.CPU
