from app.services.interview_generation_store import ChunkCoalescer


def test_chunk_coalescer_uses_injected_clock():
    now = [0.0]
    coalescer = ChunkCoalescer(
        max_interval_seconds=0.2,
        clock=lambda: now[0],
    )

    assert coalescer.add("a") is None
    now[0] = 0.3
    assert coalescer.add("b") == "ab"
    assert coalescer.flush() is None
