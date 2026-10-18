"""Dev helper: send one websocket message to the test HA.
    docker exec -e TOK="$HA_TEST_TOKEN" lighting-curves-ha python3 /config/ws.py '{"type": "..."}'"""
import asyncio, json, os, sys

import aiohttp


async def main():
    async with aiohttp.ClientSession() as s:
        ws = await s.ws_connect("http://localhost:8123/api/websocket")
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": os.environ["TOK"]})
        await ws.receive_json()
        await ws.send_json({"id": 1, **json.loads(sys.argv[1])})
        print(json.dumps(await ws.receive_json()))


asyncio.run(main())
