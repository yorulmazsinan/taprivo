from taprivo.mcp.security import TokenBucket


def test_bucket_allows_burst_then_refills() -> None:
    now = [0.0]
    bucket = TokenBucket(rate_per_second=2, burst=3, clock=lambda: now[0])
    assert [bucket.take() for _ in range(4)] == [True, True, True, False]
    now[0] += 0.5
    assert bucket.take() is True
    assert bucket.take() is False
    now[0] += 10
    assert [bucket.take() for _ in range(3)] == [True, True, True]
