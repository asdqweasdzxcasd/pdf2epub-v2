"""RQ 워커 부트스트랩.

실행: python -m app.worker
"""

import logging

from redis import Redis
from rq import Worker

from app.config import settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    redis = Redis.from_url(settings.REDIS_URL)
    worker = Worker([settings.RQ_QUEUE], connection=redis)
    worker.work()


if __name__ == "__main__":
    main()
