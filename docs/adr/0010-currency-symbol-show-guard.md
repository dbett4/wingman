# ADR-0010 — Currency symbol show guard

Date: 2026-06-19
Status: Accepted

## Context

Workiva value-format payloads can carry both `showCurrencySymbol` and `currencySymbol`. The automatic formatter sometimes copies a neighbor's whole `valueFormat` as the planned write. If that copied format includes `currencySymbol` while `showCurrencySymbol` is false or absent, Workiva can still display the symbol in body rows where it does not belong.

This is different from restore/revert. Revert must write the captured before-state faithfully. The guard only applies to planned forward writes.

## Decision

Before any forward value-format write on the copy/parens paths, Wingman now sanitizes the planned format:

```python
def strip_unshown_currency_symbol(value_format):
    vf = dict(value_format or {})
    if vf.get("showCurrencySymbol") is not True:
        vf.pop("currencySymbol", None)
    return vf
```

Applied to:

- `fix_format_copy(...)`
- `fix_negative_parens(...)`

Not applied to:

- captured before-state
- revert payloads
- explicit `showCurrencySymbol: true` formats

## Verification

Unit tests cover:

- hidden symbols are stripped without mutating the source dict
- explicit visible symbols are preserved
- `fix_format_copy` dry-run emits a sanitized `valueFormat`
- `fix_negative_parens` dry-run emits a sanitized `valueFormat`

Run:

```bash
python3 -m pytest server/test_fixer.py
```
