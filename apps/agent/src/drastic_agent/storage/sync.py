"""Replace synced tables as one transaction, retaining the last good offline config."""


def replace_configuration(database, table_rows, *, legacy_secrets, backup_owner_uuid):
    with database:
        legacy_secrets.delete()
        for table, rows in table_rows:
            table.delete()
            # dataset.insert_many uses a bound connection outside this thread's transaction.
            for row in rows:
                table.insert(row)
        database["agent"].upsert({"name": "backup_owner_uuid", "settings": backup_owner_uuid}, ["name"])
