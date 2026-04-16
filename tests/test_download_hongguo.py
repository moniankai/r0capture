"""Tests for download_hongguo.py"""
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_hookstate_get_after_fence_empty():
    from scripts.download_hongguo import HookState
    state = HookState()
    ref, url, key = state.get_after_fence(time.time())
    assert ref is None
    assert url is None
    assert key is None


def test_hookstate_get_after_fence_filters_old_data():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey
    state = HookState()
    old_ts = time.time() - 10
    with state.lock:
        state.refs.append(VideoRef(video_id="old_vid", duration=100, timestamp=old_ts))
        state.urls.append(VideoURL(video_id="old_vid", url="http://old", quality="720p", kid="aaa", timestamp=old_ts))
        state.keys.append(AESKey(key_hex="0" * 32, bits=128, timestamp=old_ts))

    fence_ts = time.time() - 5
    ref, url, key = state.get_after_fence(fence_ts)
    assert ref is None
    assert url is None
    assert key is None


def test_hookstate_get_after_fence_returns_new_data():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey
    state = HookState()
    fence_ts = time.time() - 1
    new_ts = time.time()
    with state.lock:
        state.refs.append(VideoRef(video_id="new_vid", duration=60, timestamp=new_ts))
        state.urls.append(VideoURL(video_id="new_vid", url="http://new/1080p", quality="1080p", kid="bbb", timestamp=new_ts))
        state.urls.append(VideoURL(video_id="new_vid", url="http://new/360p", quality="360p", kid="bbb", timestamp=new_ts))
        state.keys.append(AESKey(key_hex="a" * 32, bits=128, timestamp=new_ts))

    ref, url, key = state.get_after_fence(fence_ts)
    assert ref is not None
    assert ref.video_id == "new_vid"
    assert url == "http://new/1080p"
    assert key is not None
    assert key.key_hex == "a" * 32


def test_hookstate_quality_ordering():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey
    state = HookState()
    fence_ts = time.time() - 1
    ts = time.time()
    with state.lock:
        state.refs.append(VideoRef(video_id="vid1", duration=60, timestamp=ts))
        state.urls.append(VideoURL(video_id="vid1", url="http://360", quality="360p", kid="k", timestamp=ts))
        state.urls.append(VideoURL(video_id="vid1", url="http://720", quality="720p", kid="k", timestamp=ts))
        state.urls.append(VideoURL(video_id="vid1", url="http://480", quality="480p", kid="k", timestamp=ts))
        state.keys.append(AESKey(key_hex="b" * 32, bits=128, timestamp=ts))

    ref, url, key = state.get_after_fence(fence_ts)
    assert url == "http://720"
