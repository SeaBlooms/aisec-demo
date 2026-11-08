# Security

This repository is a **local demo**. Tool calls, "sent" email, and third-party MCP payloads stay in a sandbox on the machine that runs it. Nothing is meant to leave that laptop.

## Reporting a vulnerability

Please report security issues in this project through GitHub's private vulnerability reporting on the repository, or by opening an issue that does **not** include exploit code.

Do not file exploit proofs-of-concept against third-party MCP servers, models, or products in this tracker. This demo is for illustrating default-deny runtime control, not for attacking other systems.

## Secrets

Copy `.env.example` to `.env` and put your own `ANTHROPIC_API_KEY` there. `.env` is gitignored and must not be committed.
