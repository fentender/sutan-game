import tempfile
from pathlib import Path

from src.core.mod.scanner import scan_single_mod


def test_scan_single_mod_with_control_char_in_info_json() -> None:
    """Info.json 含控制字符时应降级而非崩溃"""
    with tempfile.TemporaryDirectory() as tmp:
        mod_dir = Path(tmp) / "12345"
        mod_dir.mkdir()
        (mod_dir / "config").mkdir()
        (mod_dir / "Info.json").write_bytes(
            b'{"name": "test\x01mod", "description": "desc"}',
        )
        result = scan_single_mod(mod_dir)
        assert result is not None
        assert result.name == "12345"


def test_parse_file_as_dict_with_control_char() -> None:
    """本体 config 含控制字符时 _parse_file_as_dict 应返回 None 而非崩溃"""
    from src.core.schema.generator import _parse_file_as_dict

    with tempfile.TemporaryDirectory() as tmp:
        bad_json = Path(tmp) / "test.json"
        bad_json.write_bytes(b'{"key": "value\x01here"}')
        result = _parse_file_as_dict(str(bad_json))
        assert result is None
