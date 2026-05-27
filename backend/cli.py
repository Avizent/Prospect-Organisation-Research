"""CLI entry point for ans-tool.

Commands:
    db upgrade          Run Alembic migrations
    audit week          Spend + trap summary, exports CSV
    delete-contact <id> GDPR contact deletion (audit-logged)
    delete-company <id> GDPR company cascade delete
    export-person <name> SAR export for a named individual

Entry point registered in pyproject.toml: ans-tool = "backend.cli:main"

Implemented in step 2 (db upgrade) and step 14 (GDPR commands).
"""
# TODO: implement in steps 2 and 14
