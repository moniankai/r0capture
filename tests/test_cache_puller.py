import pytest
from scripts.cache_puller import list_remote_mdl_with_time

def test_list_remote_mdl_with_time(mocker):
    """测试列出远程 .mdl 文件及修改时间"""
    mock_run = mocker.patch('scripts.cache_puller.run_adb')
    mock_run.return_value.stdout = """
-rw-rw---- 1 u0_a163 sdcard_rw 1048576 2026-04-16 10:25 /sdcard/Android/data/com.phoenix.read/cache/short/file1.mdl
-rw-rw---- 1 u0_a163 sdcard_rw 2097152 2026-04-16 14:07 /sdcard/Android/data/com.phoenix.read/cache/short/file2.mdl
"""
    mock_run.return_value.returncode = 0

    files = list_remote_mdl_with_time()

    assert len(files) == 2
    assert files[0]['name'] == 'file1.mdl'
    assert files[0]['size'] == '1048576'
    assert files[0]['date'] == '2026-04-16 10:25'
    assert files[1]['name'] == 'file2.mdl'


def test_pull_and_sort_cache(mocker, tmp_path):
    """测试拉取并排序缓存文件"""
    mock_list = mocker.patch('scripts.cache_puller.list_remote_mdl_with_time')
    mock_list.return_value = [
        {"name": "file2.mdl", "date": "2026-04-16 14:07", "path": "/path/file2.mdl"},
        {"name": "file1.mdl", "date": "2026-04-16 10:25", "path": "/path/file1.mdl"},
    ]

    mock_run = mocker.patch('scripts.cache_puller.run_adb')

    from scripts.cache_puller import pull_and_sort_cache
    output_dir = str(tmp_path / "cache")

    result = pull_and_sort_cache(output_dir)

    assert result == 2
    assert (tmp_path / "cache" / "concat_list.txt").exists()
    assert mock_run.call_count == 2  # 拉取两个文件
