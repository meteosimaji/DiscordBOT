import ast
import datetime
import pytest

class DummyChannel:
    def __init__(self):
        self.sent = None
    async def send(self, *, embed=None):
        self.sent = embed

class DummyColour:
    def __init__(self, name):
        self.name = name
    @staticmethod
    def light_grey():
        return DummyColour('light_grey')
    @staticmethod
    def red():
        return DummyColour('red')
    @staticmethod
    def orange():
        return DummyColour('orange')
    @staticmethod
    def gold():
        return DummyColour('gold')
    @staticmethod
    def green():
        return DummyColour('green')

class DummyEmbed:
    def __init__(self, title='', colour=None):
        self.title = title
        self.colour = colour
        self.fields = []
        self.image = None
    def add_field(self, name, value, inline=True):
        self.fields.append({'name': name, 'value': value, 'inline': inline})
    def set_image(self, url):
        self.image = url

class DummyDiscord:
    Colour = DummyColour
    Embed = DummyEmbed
    class TextChannel:
        pass

def _make_aiohttp(detail):
    class DummyResponse:
        def __init__(self, data):
            self._data = data
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass
        async def json(self, content_type=None):
            return self._data
    class DummySession:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc, tb):
            pass
        def get(self, url, timeout=10):
            return DummyResponse(detail)
    class DummyAiohttp:
        def ClientSession(self):
            return DummySession()
    return DummyAiohttp()

def _load_send_eew(aiohttp_mod, discord_mod):
    with open('DiscordYONE.py', 'r', encoding='utf-8') as f:
        source = f.read()
    module = ast.parse(source)
    func_node = None
    for node in module.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == '_send_eew':
            func_node = node
            break
    if func_node is None:
        raise RuntimeError('_send_eew not found')
    namespace = {
        'aiohttp': aiohttp_mod,
        'discord': discord_mod,
        'datetime': datetime,
        'EEW_BASE_URL': 'https://example.com/'
    }
    exec(compile(ast.Module(body=[func_node], type_ignores=[]), filename='_send_eew', mode='exec'), namespace)
    return namespace['_send_eew']

def _make_detail():
    return {
        'Head': {'TargetDateTime': '2024-01-02T12:34:00+09:00', 'Title': 'title'},
        'Body': {
            'Earthquake': {'Hypocenter': {'Area': {'Name': 'area'}}, 'Magnitude': '5.0'},
            'Intensity': {'Observation': {'MaxInt': '3'}},
        },
    }

def test_send_eew_with_ctt():
    aiohttp_mod = _make_aiohttp(_make_detail())
    send_eew = _load_send_eew(aiohttp_mod, DummyDiscord)
    channel = DummyChannel()
    item = {'json': 'x.json', 'ctt': '20240102123456'}
    import asyncio
    asyncio.run(send_eew(channel, item))
    assert channel.sent.fields[0]['value'] == '2024年01月02日(Tue)12:34:56'

def test_send_eew_without_ctt():
    aiohttp_mod = _make_aiohttp(_make_detail())
    send_eew = _load_send_eew(aiohttp_mod, DummyDiscord)
    channel = DummyChannel()
    item = {'json': 'x.json'}
    import asyncio
    asyncio.run(send_eew(channel, item))
    assert channel.sent.fields[0]['value'] == '2024年01月02日(Tue)12:34:00'
