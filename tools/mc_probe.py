import asyncio, json, sys
from meshcore import MeshCore

HOST, PORT = "192.168.1.93", 5000

async def main():
    print("connecting to %s:%d ..." % (HOST, PORT))
    try:
        mc = await asyncio.wait_for(MeshCore.create_tcp(HOST, PORT), timeout=25)
    except Exception as e:
        print("CONNECT FAILED: %s: %s" % (type(e).__name__, e))
        return 1

    print("connected:", mc.is_connected)
    info = dict(mc.self_info or {})
    # never print anything key-like in full
    for k in list(info):
        if "priv" in k.lower() or "secret" in k.lower():
            info[k] = "<redacted>"
    print("--- self_info ---")
    print(json.dumps(info, indent=1, default=str))

    try:
        await asyncio.wait_for(mc.ensure_contacts(), timeout=25)
        cs = mc.contacts or {}
        print("--- contacts: %d ---" % len(cs))
        for k, c in list(cs.items())[:15]:
            if isinstance(c, dict):
                print("  %-22s type=%s last=%s" % (
                    str(c.get("adv_name", "?"))[:22],
                    c.get("type"), c.get("last_advert")))
    except Exception as e:
        print("contacts failed: %s: %s" % (type(e).__name__, e))

    await mc.disconnect()
    print("disconnected cleanly")
    return 0

sys.exit(asyncio.run(main()))
