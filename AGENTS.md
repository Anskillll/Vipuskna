# Project workflow

- After every code change, run the complete Django test suite with `.venv\Scripts\python.exe manage.py test --noinput`, plus `manage.py check`. Fix failures before publishing.
- Commit and push completed changes to the existing GitHub repository. Deploy site changes to the existing OrthoSmile server as previously authorized by the user.
- For database-specific fixes, verify against PostgreSQL in an isolated test database as well as local SQLite. Never run tests against production data.
- Preserve the full-screen photographic homepage when refining the visual design.
