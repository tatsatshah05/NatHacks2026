import os

# Tests never touch a real database or the network: use in-memory SQLite.
os.environ["DATABASE_URL"] = "sqlite://"
