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


def test_on_message_video_ref():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "send", "payload": {
        "t": "video_ref",
        "data": {"mVideoId": "v_test_123", "mVideoDuration": "90"},
        "episode_number": 3,
    }}, None)
    assert len(state.refs) == 1
    assert state.refs[0].video_id == "v_test_123"
    assert state.refs[0].duration == 90
    assert state.current_video_id == "v_test_123"


def test_on_message_video_info():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "send", "payload": {
        "t": "video_ref",
        "data": {"mVideoId": "v_abc", "mVideoDuration": "60"},
    }}, None)
    handler({"type": "send", "payload": {
        "t": "video_info",
        "idx": 0,
        "data": {"mMainUrl": "https://cdn/video.mp4", "mResolution": "1080p", "mKid": "kid123"},
    }}, None)
    assert len(state.urls) == 1
    assert state.urls[0].video_id == "v_abc"
    assert state.urls[0].url == "https://cdn/video.mp4"
    assert state.urls[0].quality == "1080p"


def test_on_message_aes_key():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "send", "payload": {
        "t": "AES_KEY",
        "key": "abcd1234abcd1234abcd1234abcd1234",
        "bits": 128,
        "dec": 0,
        "episode_number": 1,
    }}, None)
    assert len(state.keys) == 1
    assert state.keys[0].key_hex == "abcd1234abcd1234abcd1234abcd1234"
    assert state.keys[0].bits == 128


def test_on_message_ignores_non_send():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "error", "description": "something"}, None)
    assert len(state.refs) == 0
    assert len(state.keys) == 0


def test_on_message_video_info_empty_url_ignored():
    from scripts.download_hongguo import HookState, create_on_message
    state = HookState()
    handler = create_on_message(state)
    handler({"type": "send", "payload": {
        "t": "video_ref",
        "data": {"mVideoId": "v1"},
    }}, None)
    handler({"type": "send", "payload": {
        "t": "video_info",
        "idx": 0,
        "data": {"mMainUrl": "", "mResolution": "360p"},
    }}, None)
    assert len(state.urls) == 0


def test_wait_capture_returns_none_on_empty_state():
    from scripts.download_hongguo import HookState, wait_capture
    state = HookState()
    result = wait_capture(state, time.time(), timeout=1)
    assert result is None


def test_wait_capture_returns_data_when_available():
    from scripts.download_hongguo import HookState, VideoRef, VideoURL, AESKey, wait_capture
    state = HookState()
    fence_ts = time.time()
    def add_data():
        time.sleep(0.3)
        ts = time.time()
        with state.lock:
            state.refs.append(VideoRef(video_id="vid1", duration=60, timestamp=ts))
            state.urls.append(VideoURL(video_id="vid1", url="http://test", quality="720p", kid="k1", timestamp=ts))
            state.keys.append(AESKey(key_hex="a" * 32, bits=128, timestamp=ts))
    t = threading.Thread(target=add_data)
    t.start()
    result = wait_capture(state, fence_ts, timeout=5)
    t.join()
    assert result is not None
    ref, url, key = result
    assert ref.video_id == "vid1"
    assert url == "http://test"
    assert key.key_hex == "a" * 32


def test_build_plan_skips_existing():
    from scripts.download_hongguo import build_plan
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        ep_file = Path(tmpdir) / "episode_003_abcd1234.mp4"
        ep_file.write_bytes(b"\x00" * 200_000)
        plan = build_plan(tmpdir, total_eps=5, start_ep=1)
        statuses = {p["ep"]: p["status"] for p in plan}
        assert statuses[3] == "done"
        assert statuses[1] == "pending"
        assert statuses[5] == "pending"
