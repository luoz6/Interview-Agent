from __future__ import annotations


class PostgresPrincipalMemoryMigrationProbe:
    def __init__(
        self,
        *,
        connection_provider,
        table_prefix: str,
        migration_id: str,
        checksum: str,
    ) -> None:
        self.connection_provider = connection_provider
        self.table = f"{table_prefix}_schema_migrations"
        self.migration_id = migration_id
        self.checksum = checksum

    def is_current(self) -> bool:
        try:
            from psycopg2 import sql

            with self.connection_provider.connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            "SELECT checksum FROM {table} WHERE migration_id=%s"
                        ).format(table=sql.Identifier(self.table)),
                        (self.migration_id,),
                    )
                    row = cursor.fetchone()
            return bool(row and row[0] == self.checksum)
        except Exception:
            return False
