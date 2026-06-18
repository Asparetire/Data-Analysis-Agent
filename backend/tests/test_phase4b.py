"""Phase 4B: 限流 + 日志脱敏 测试。"""

from __future__ import annotations

import logging

import pytest
from app.services import lineage, metadata_service
from app.utils import log_scrub, rate_limit

# ---------------------------------------------------------------------------
# Rate limit: sliding window
# ---------------------------------------------------------------------------


async def test_check_rate_limit_allows_under_cap(fake_redis):
    ok, count = await rate_limit.check_rate_limit(fake_redis, "rl:test", max_count=3)
    assert ok is True
    assert count == 1

    ok, count = await rate_limit.check_rate_limit(fake_redis, "rl:test", max_count=3)
    assert ok is True
    assert count == 2

    ok, count = await rate_limit.check_rate_limit(fake_redis, "rl:test", max_count=3)
    assert ok is True
    assert count == 3


async def test_check_rate_limit_blocks_over_cap(fake_redis):
    for _ in range(3):
        await rate_limit.check_rate_limit(fake_redis, "rl:test", max_count=3)
    ok, count = await rate_limit.check_rate_limit(fake_redis, "rl:test", max_count=3)
    assert ok is False
    assert count == 4


async def test_check_rate_limit_uses_separate_keys(fake_redis):
    await rate_limit.check_rate_limit(fake_redis, "rl:user:a", max_count=1)
    # Different key — independent counter.
    ok, _ = await rate_limit.check_rate_limit(fake_redis, "rl:user:b", max_count=1)
    assert ok is True


async def test_check_rate_limit_fails_open_on_redis_error(monkeypatch):
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("redis down")

    class _BrokenRedis:
        def pipeline(self):
            class _Pipe:
                zremrangebyscore = staticmethod(_boom)
                zadd = staticmethod(_boom)
                zcard = staticmethod(_boom)
                expire = staticmethod(_boom)

                async def execute(self):
                    raise RuntimeError("redis down")

            return _Pipe()

    ok, count = await rate_limit.check_rate_limit(_BrokenRedis(), "rl:x", max_count=1)
    assert ok is True
    assert count == 0


# ---------------------------------------------------------------------------
# Log scrub: pattern coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected_substring",
    [
        ("Authorization: Bearer eyJhbGc.payload.sig", "Bearer ***"),
        ("OpenAI key sk-abc123def456ghi789jkl012mno345", "sk-***"),
        ("contact alice@example.com for details", "***@***.***"),
        ("phone 13812345678 call me", "1**********"),
        ("id 110101199003070123 here", "******************"),
        ("card 4111111111111111 here", "****"),
    ],
)
def test_scrub_redacts_each_pattern(raw, expected_substring):
    out = log_scrub.scrub(raw)
    assert expected_substring in out
    # The sensitive substring must NOT appear verbatim in the scrubbed output.
    # Pick the obvious secret token from each input and verify it's gone.
    if "Bearer " in raw:
        assert "eyJhbGc" not in out
    if "sk-" in raw:
        assert "sk-abc123" not in out
    if "@" in raw:
        assert "alice@example.com" not in out
    if "13812345678" in raw:
        assert "13812345678" not in out
    if "110101199003070123" in raw:
        assert "110101199003070123" not in out
    if "4111111111111111" in raw:
        assert "4111111111111111" not in out


def test_scrub_leaves_innocent_text_alone():
    text = "user uploaded sales.csv with 1234 rows"
    assert log_scrub.scrub(text) == text


def test_scrub_filter_rewrites_record_message():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="login as alice@example.com token=Bearer abc.def.ghi",
        args=(),
        exc_info=None,
    )
    filt = log_scrub.ScrubFilter()
    assert filt.filter(record) is True
    msg = record.getMessage()
    assert "alice@example.com" not in msg
    assert "Bearer ***" in msg
    assert "***@***.***" in msg


# ---------------------------------------------------------------------------
# Lineage: user_id propagation
# ---------------------------------------------------------------------------


def test_record_query_stamps_user_id(tmp_data_dir):
    lineage.record_query(
        source_ids=["ds-1"],
        sql="SELECT 1",
        ok=True,
        row_count=1,
        duration_ms=0.5,
        user_id="user-abc",
    )
    entries = metadata_service.get_lineage("ds-1")
    assert len(entries) == 1
    assert entries[0]["user_id"] == "user-abc"


def test_record_query_omits_user_id_when_none(tmp_data_dir):
    lineage.record_query(
        source_ids=["ds-1"],
        sql="SELECT 1",
        ok=True,
        row_count=1,
    )
    entries = metadata_service.get_lineage("ds-1")
    assert len(entries) == 1
    assert "user_id" not in entries[0]
