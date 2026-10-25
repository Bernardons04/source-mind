import asyncio
from sqlalchemy import text
from app.database import engine, Base
import app.models  # Ensure all models are registered with Base metadata


async def init_database():
    """Initializes extensions and creates all tables if they do not exist."""
    print("Connecting to database and applying schema...")
    async with engine.begin() as conn:
        # Enable pgvector and uuid extensions
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "vector";'))
        # Create tables from SQLAlchemy models
        await conn.run_sync(Base.metadata.create_all)
        # Create full-text and vector indexes if not exist
        try:
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_doc_chunks_fts ON document_chunks USING gin (to_tsvector('english', content));"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding ON document_chunks USING hnsw (embedding vector_cosine_ops);"
                )
            )
        except Exception as e:
            print(f"Index creation note (may already exist or will index on demand): {e}")

    print("Database schema successfully initialized.")


if __name__ == "__main__":
    asyncio.run(init_database())
