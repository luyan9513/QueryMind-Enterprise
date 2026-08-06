# Chinook evaluation data

This directory contains the PostgreSQL script used for the QueryMind v0.8
second-data-source evaluation.

- Upstream project: https://github.com/lerocha/chinook-database
- Upstream version embedded in the script: `1.4.5`
- Source file: `ChinookDatabase/DataSources/Chinook_PostgreSql.sql`
- Download date: `2026-07-30`
- SHA-256: `e3fde5c1a5b51a2a91429a702c9ca6e69ba56e6c7f5e112724d70c3d03db695e`
- License: MIT; the upstream `LICENSE.md` is included next to the script.

Safety note: the upstream script starts with `DROP DATABASE IF EXISTS chinook`.
Do not execute it directly in an environment that may already contain a
database named `chinook`. The v0.8 local setup first verifies that the database
does not exist, creates it separately, and executes only the table/data section
inside a transaction.
