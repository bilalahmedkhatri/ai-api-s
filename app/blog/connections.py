"""Worker-lifetime, lazy blog engines; no sessions or permission results are cached."""

import asyncio

from app.blog.database import create_blog_engine


class BlogEngines:
    def __init__(self):
        self._engines = {}

    async def get(self, target):
        url = target.database_url.get_secret_value()
        previous = self._engines.get(target.key)
        if previous is not None and previous[0] == url:
            return previous[1]
        engine = create_blog_engine(url, reusable=True)
        self._engines[target.key] = (url, engine)
        if previous is not None:
            await previous[1].dispose()
        return engine

    async def close(self):
        engines, self._engines = self._engines, {}
        await asyncio.gather(*(engine.dispose() for _, engine in engines.values()))
