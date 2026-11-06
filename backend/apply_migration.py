import asyncio
from urllib.parse import urlparse
from sqlalchemy import text
from app.config import settings
from app.database import engine

REQUIRED_TABLES = [
    "assistants",
    "documents",
    "document_chunks",
    "document_outbox",
    "chats",
    "messages",
    "message_events",
    "message_citations",
    "user_preferences",
]


def mask_db_url(url: str) -> str:
    """Masks password from connection string for safe diagnostic display."""
    try:
        parsed = urlparse(url)
        netloc = ""
        if parsed.username:
            netloc += parsed.username
            if parsed.password:
                netloc += ":****"
            netloc += "@"
        if parsed.hostname:
            netloc += parsed.hostname
        if parsed.port:
            netloc += f":{parsed.port}"
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return "postgresql://***masked***"


async def check_and_apply_migration():
    masked_target = mask_db_url(settings.DATABASE_URL)
    print(f"Target Database: {masked_target}")

    async with engine.connect() as conn:
        # 1. Verify connection and current DB context
        res = await conn.execute(
            text(
                "SELECT current_database(), current_user, current_schema(), version();"
            )
        )
        row = res.fetchone()
        db_name, db_user, schema, pg_version = row[0], row[1], row[2], row[3]
        print(f"Connected successfully to DB '{db_name}' as user '{db_user}' (schema: '{schema}')")
        print(f"PostgreSQL version: {pg_version.split(',')[0]}")

        # 2. Check existing extensions
        ext_res = await conn.execute(
            text("SELECT extname, extversion FROM pg_extension;")
        )
        existing_extensions = {r[0]: r[1] for r in ext_res.fetchall()}
        print(f"Installed extensions before migration: {list(existing_extensions.keys())}")

        # 3. Check existing tables
        tables_res = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
            )
        )
        existing_tables = set(r[0] for r in tables_res.fetchall())
        print(f"Existing public tables before migration: {sorted(list(existing_tables))}")

        # 4. Read and parse 001_initial_schema.sql safely
        print("\n--- Applying migration backend/migrations/001_initial_schema.sql ---")
        with open("migrations/001_initial_schema.sql", "r", encoding="utf-8") as f:
            sql_content = f.read()

        # Remove single-line comments first
        clean_lines = []
        for line in sql_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            clean_lines.append(line)
        clean_sql = "\n".join(clean_lines)

        # Split statements by semicolon
        statements = [s.strip() for s in clean_sql.split(";") if s.strip()]

        executed_count = 0
        for stmt in statements:
            summary = [l.strip() for l in stmt.split("\n") if l.strip()][0][:70]
            try:
                await conn.execute(text(stmt))
                await conn.commit()
                executed_count += 1
                print(f"  [OK] {summary}")
            except Exception as e:
                await conn.rollback()
                print(f"  [ERROR/NOTICE] {summary} -> {e}")

        print(f"\nSuccessfully executed {executed_count} statements.")

        # 5. Verification after migration
        print("\n--- Post-migration verification ---")
        post_ext = await conn.execute(
            text("SELECT extname, extversion FROM pg_extension;")
        )
        post_extensions = {r[0]: r[1] for r in post_ext.fetchall()}
        print(f"Installed extensions: {list(post_extensions.keys())}")

        post_tables_res = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';"
            )
        )
        final_tables = set(r[0] for r in post_tables_res.fetchall())
        print(f"Final public tables: {sorted(list(final_tables))}")

        # Check required tables
        missing_tables = [t for t in REQUIRED_TABLES if t not in final_tables]
        if missing_tables:
            print(f"WARNING: Missing tables: {missing_tables}")
        else:
            print("SUCCESS: All 9 required ManualMind tables are present in the database!")

        # Check vector and full-text indexes on document_chunks
        idx_res = await conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'document_chunks';"
            )
        )
        chunk_indexes = {r[0]: r[1] for r in idx_res.fetchall()}
        print(f"Indexes on document_chunks: {list(chunk_indexes.keys())}")

        # Check vector column type
        col_res = await conn.execute(
            text(
                "SELECT column_name, udt_name FROM information_schema.columns WHERE table_name = 'document_chunks' AND column_name = 'embedding';"
            )
        )
        col_info = col_res.fetchone()
        if col_info:
            print(f"Embedding column confirmed: '{col_info[0]}' type: '{col_info[1]}'")

        return {
            "status": "success" if not missing_tables else "partial",
            "db_name": db_name,
            "db_user": db_user,
            "installed_extensions": list(post_extensions.keys()),
            "present_tables": sorted(list(final_tables)),
            "missing_tables": missing_tables,
            "chunk_indexes": list(chunk_indexes.keys()),
        }


if __name__ == "__main__":
    asyncio.run(check_and_apply_migration())
