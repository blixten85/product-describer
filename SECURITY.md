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

- Always pass `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` and other secrets via environment variables
- Never commit `.env` files, the `config/` directory, or credentials to version control
- Keep dependencies updated (Dependabot is enabled)
