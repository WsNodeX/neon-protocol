# Security Policy

## Supported versions

Only the latest tagged release receives fixes. This project is at `v1.0.0`.

## Reporting a vulnerability

Do not open a public issue. Use GitHub's private reporting:
**Security** tab → **Report a vulnerability**.

That opens a private advisory only you and the maintainer can see.

Please include:

- what the issue is and which component it affects
- a minimal reproduction, or the exact input that triggers it
- the impact you believe it has

Expect an initial response within a week. This is a personal project, not a
funded one, so a fix may take longer than that — but you will hear back.

## Scope

This library is designed to run in-process on inputs the caller controls. What it
does *not* claim:

- It has not been fuzzed.
- It does not sandbox untrusted input.
- It is not hardened against a hostile caller running in the same process.

Reports that amount to "untrusted input can crash it" are expected behaviour
unless the README claims otherwise — quote the README line if you think it does.
