"""
Entrypoint script for running the ManualMind Outbox Dispatcher service.
"""
from app.workers.dispatcher import main

if __name__ == "__main__":
    main()

