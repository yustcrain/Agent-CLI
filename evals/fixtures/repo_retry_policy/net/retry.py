from dataclasses import dataclass
import time

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError)
    delays: tuple[float, ...] = (0.0,)

def execute(operation, policy=RetryPolicy(), sleep=time.sleep):
    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except Exception:
            if attempt + 1 == policy.max_attempts:
                raise
            sleep(policy.delays[0])
