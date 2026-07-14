from app.settings import Settings, get_settings


def test_settings_defaults_are_local_only():
    s = get_settings()
    assert s.host == "127.0.0.1"
    assert ".docx" in s.allowed_extensions
    assert s.max_file_bytes == 100 * 1024 * 1024
    assert s.max_pages == 300
    assert "不是正式审查结论" in s.disclaimer


def test_storage_override(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    s = get_settings()
    assert s.storage_root == tmp_path.resolve()
    assert s.db_path == tmp_path.resolve() / "review.db"
    get_settings.cache_clear()
