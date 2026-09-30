import asyncio

from pmanalysis.feeds.gamma import GammaClient


async def main() -> None:
    client = GammaClient()
    windows = await client.discover_active_windows()
    print(f"discovered {len(windows)} windows")
    for item in windows[:9]:
        print(f"  {item.asset} {item.interval} {item.slug}")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
