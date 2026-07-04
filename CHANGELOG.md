# Changelog

## 1.0.2 - 2026-07-04

- Clarify previous memory extraction failure notices so prompt context says the failure is from a previous extraction attempt.
- Limit previous extraction failure notices to `kind` and `reason`; omit provider, model, detail, and hint text from prompt context.
- Add a SessionStart prologue rule instructing agents to read garbled or misdecoded topic JSON files explicitly as UTF-8.
