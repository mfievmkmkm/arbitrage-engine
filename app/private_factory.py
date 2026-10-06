from .exchange_names import exchange_class
from .credentials import configured, credentials
from .private_reader import PrivateReader


def build_private_readers():
    readers = {}
    clients = {}
    for name in configured():
        auth = credentials(name)
        params = {
            "apiKey": auth["apiKey"],
            "secret": auth["secret"],
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
        if auth["password"]:
            params["password"] = auth["password"]
        client = exchange_class(name)(params)
        clients[name] = client
        readers[name] = PrivateReader(name, client)
    return readers, clients


async def close_clients(clients):
    for c in clients.values():
        try:
            await c.close()
        except Exception:
            pass
