import shelve

_DB = shelve.open("threads.db")


def get(channel_id: int) -> str | None:
    return _DB.get(str(channel_id))


def save(channel_id: int, thread_id: str):
    _DB[str(channel_id)] = thread_id
