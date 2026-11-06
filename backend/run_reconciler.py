"""
Entrypoint script for running the ManualMind Abandoned Upload Reconciliation service.
"""
from app.workers.reconciliation import main

if __name__ == "__main__":
    main()

