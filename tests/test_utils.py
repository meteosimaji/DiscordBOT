import ast
import re
import pytest


def _load_parse_seek_time():
    with open('DiscordYONE.py', 'r', encoding='utf-8') as f:
        source = f.read()
    module = ast.parse(source)
    func_node = None
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'parse_seek_time':
            func_node = node
            break
    if func_node is None:
        raise RuntimeError('parse_seek_time not found')
    func_module = ast.Module(body=[func_node], type_ignores=[])
    namespace = {'re': re}
    exec(compile(func_module, filename='parse_seek_time', mode='exec'), namespace)
    return namespace['parse_seek_time']


parse_seek_time = _load_parse_seek_time()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1m30s", 90),
        ("2:00", 120),
        ("1h2m3s", 3723),
        ("1:02:03", 3723),
        ("90", 90),
        ("1h", 3600),
        ("2m", 120),
        ("3s", 3),
    ],
)
def test_parse_seek_time_valid(text, expected):
    assert parse_seek_time(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "abc",
        "",
        "1h2h",
        "1m2",
        "1ms",
    ],
)
def test_parse_seek_time_invalid(text):
    with pytest.raises(ValueError):
        parse_seek_time(text)
