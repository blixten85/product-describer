# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | ✅ Yes    |

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not** open a public issue.

Use [GitHub's private reporting feature](https://github.com/blixten85/product-describer/security/advisories/new) to report it confidentially.

You should receive a response within 48 hours. If the issue is confirmed, a patch will be released as soon as possible.

## Security Best Practices

- Always pass `ANTHROPIC_API_KEY` (if used) and other secrets via environment variables
- Never commit `.env` files or credentials to version control
- The app does not expose Ollama externally — keep port 11434 firewalled
- Keep dependencies updated (Dependabot is enabled)
