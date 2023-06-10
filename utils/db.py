import aiosqlite


class Database:
    """Generic database holder."""
    def __init__(self):
        self.conn = None

    async def connect(self, *args, **kwargs):
        if self.conn is not None:
            if not self.conn.is_closed():
                raise RuntimeError("Database connection already open")
        self.conn = aiosqlite.connect(*args, **kwargs)

    async def close(self):
        await self.conn.close()
        self.conn = None

    async def execute(self, *args, **kwargs):
        """Shim for self.conn.execute"""
        return await self.conn.execute(*args, **kwargs)

    async def create_all_tables(self):
        """Creates all tables in the database if they do not exist."""
        scripts = []
        for script in scripts:
            await self.execute(script)
