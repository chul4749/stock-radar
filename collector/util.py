import sys
from datetime import timedelta, timezone

KST = timezone(timedelta(hours=9))


def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()


EOK = 1e8  # 1억 원
