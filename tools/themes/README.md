# City themes

Declarative color/font theme packs shared by the hub, the attribute editor and
the texture editor. A municipality can restyle every screen to match its visual
identity without touching any code.

## How a city applies a theme

Place a `theme.json` at the city repository root — it applies to everyone just
by cloning:

```json
{ "extends": "de", "tokens": { "accent": "#0a7d33", "header_bg": "#123456" } }
```

Either extend a builtin pack (`de.json`, `us.json`, `wa.json` in this
directory) and override individual tokens, or provide a bare token object.

## Tokens (all optional)

| Token | Meaning |
|---|---|
| `name` | Display name (shown in the UI, not used for styling) |
| `bg` `card` `border` `text` `muted` `accent` | Per-screen `:root` custom properties |
| `header_bg` `header_fg` | Header band colors |
| `font` `font_head` | Body / heading font stacks |

A municipality logo works the same way: the optional `logo` field in
`4dcitygml.json` (path relative to the repo root) is resolved fail-closed by
`resolve_logo()` — invalid or missing simply means no logo.

## Security design

Themes are **declarative tokens only** — arbitrary CSS/JS is not accepted, so
script injection is structurally impossible even with third-party themes. Every
value is validated against a per-token regular expression (colors must be hex,
font stacks reject `{ } < > ;`), and invalid input is an error rather than
being silently dropped. Diagnostic colors (🔴 old / 🔵 new, diffs, check
results) are deliberately outside theming so review semantics stay uniform
across cities.

See `theme_loader.py` for the loader/validator implementation and the exact
token rules.
