from nex.config import Config, UserError
from nex.database import Database


class Authorization:
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database

    def developer(self, user: int) -> bool:
        return user == self.config.owner_id or user in self.config.developers

    async def allowed(self, user: int, guild: int, privilege: str) -> bool:
        if privilege == "code":
            return user in self.config.developers | self.config.code_users
        if self.developer(user):
            return True
        if privilege not in {"purge", "ban", "ignore"}:
            return False
        return bool(
            await self.db.rows(
                "SELECT 1 FROM trusted WHERE guild_id=? AND user_id=? AND privilege=?",
                (str(guild), str(user), privilege),
            )
        )

    async def require(self, user: int, guild: int, privilege: str):
        if not await self.allowed(user, guild, privilege):
            raise UserError("You are not authorized for this action.")

    async def protected(self, guild: int, target: int, kind: str) -> bool:
        config = self.config
        if guild in config.protected_guilds:
            return True
        if kind == "user" and target in (
            config.protected_users | config.developers | {config.owner_id, config.client_id}
        ):
            return True
        if kind == "bot" and target in config.protected_bots | {config.client_id}:
            return True
        return bool(
            await self.db.rows(
                "SELECT 1 FROM whitelist WHERE (scope='0' OR scope=?) AND "
                "((kind=? AND target_id=?) OR (kind='guild' AND target_id=?) "
                "OR (kind='user' AND target_id=?))",
                (str(guild), kind, str(target), str(guild), str(target)),
            )
        )
