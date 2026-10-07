from dataclasses import dataclass
import time

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    retry_on: tuple[type[Exception], ...] = (TimeoutError, ConnectionError)
    delays: tuple[float, ...] = (0.0,)

def execute(operation, policy=RetryPolicy(), sleep=time.sleep):
    if type(policy.max_attempts) is not int or policy.max_attempts <= 0:
        raise ValueError("max_attempts must be a positive integer")
    if not policy.delays or any(delay < 0 for delay in policy.delays):
        raise ValueError("delays must be non-empty and non-negative")
    for attempt in range(policy.max_attempts):
        try:
            return operation()
        except policy.retry_on:
            if attempt + 1 == policy.max_attempts: raise
            sleep(policy.delays[min(attempt,len(policy.delays)-1)])
