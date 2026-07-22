---
name: add-env-var
description: Add a new configuration setting to vintasend. Covers every layer a NOTIFICATION_* setting must touch — the TypedDict, DEFAULT_SETTINGS, the three framework default dicts, the NotificationSettings singleton, tests, and README — plus the env-var-wins precedence rule. Use when adding, renaming, or removing a vintasend setting, or when a setting reads as None at runtime and you need to find which layer was missed.
---

# Add a configuration setting

vintasend has no `.env` file. Configuration is resolved at runtime by
[`vintasend/app_settings.py`](../../../vintasend/app_settings.py), which detects the host framework and
reads the setting from that framework's config object — with an environment variable always taking
precedence.

A new setting is **six edits in one file plus two outside it**. Miss one and the setting silently
resolves to `None` for some frameworks and not others, which is painful to debug because it only
reproduces under the framework you didn't test.

## How resolution actually works

`get_config(setting_name, config)` calls `detect_framework()`, which tries importing Django, then
Flask, then FastAPI, and returns `"Unknown"` if none is installed. It then dispatches to
`get_django_setting` / `get_flask_setting` / `get_fastapi_setting`, each of which calls
`get_setting_with_env_var_fallback(setting_name, framework_value, <FRAMEWORK>_DEFAULT_SETTINGS)`.

That helper is:

```python
return os.getenv(
    setting_name,
    framework_value if framework_value else default_settings.get(setting_name, None),
)
```

Three consequences worth internalizing before you add anything:

1. **The environment variable always wins.** `os.getenv` is checked first, so an env var overrides
   whatever Django settings / Flask config / FastAPI config object says.
2. **Everything from the environment is a string.** There is no coercion. A setting that should be
   a list or bool arrives as `"['a','b']"` or `"False"` when it comes from the environment. If your
   setting is not a string, you must parse it explicitly — see **Non-string settings** below.
3. **Falsy framework values fall through to the default.** The check is `if framework_value`, not
   `if framework_value is not None`. A framework value of `False`, `0`, or `""` is discarded in
   favor of the default. If your setting has a meaningful falsy value, this helper is wrong for it
   and you need to handle it explicitly.
4. **When no framework is installed, `get_config` returns `{}`** — not the default. The `Unknown`
   branch returns an empty dict, so every setting resolves to `{}` in a bare-Python host. Do not
   assume `DEFAULT_SETTINGS` applies in that case.

## Checklist

All in [`vintasend/app_settings.py`](../../../vintasend/app_settings.py) unless noted.

1. **Add the key to `NotificationSettingsDict`** (the `TypedDict` near the top) with its real type.
   This is what makes mypy check the rest of your edits.

2. **Add a default to `DEFAULT_SETTINGS`.** This is the base every framework dict spreads from.
   The default must be a sensible no-op, not a real credential or a real address.

3. **Add framework-specific defaults where they differ.** The three module-level dicts —
   `DJANGO_DEFAULT_SETTINGS`, `FLASK_DEFAULT_SETTINGS`, `FASTAPI_DEFAULT_SETTINGS` — each spread
   `**DEFAULT_SETTINGS` and then override only what is framework-specific (today: the adapter,
   backend, and model import paths). **Only add your key here if the correct default genuinely
   differs per framework.** If it does not, `DEFAULT_SETTINGS` alone is enough and adding it three
   more times is duplication that will drift.

4. **Declare the attribute on the `NotificationSettings` class.** Add the annotation in the class
   body alongside the other `NOTIFICATION_*` attributes.

5. **Assign it in `NotificationSettings.__init__`**, following the existing shape:

   ```python
   self.NOTIFICATION_MY_NEW_SETTING = cast(
       str, get_config("NOTIFICATION_MY_NEW_SETTING", config)
   )
   ```

   The `cast` is load-bearing for mypy — `get_config` returns `Any`. Cast to the same type you
   declared in step 1 and step 4, and make it `str | None` if the setting is genuinely optional.

6. **Remember the singleton.** `NotificationSettings` uses `SingletonMeta`, so it is constructed
   once per process. A test that constructs it with one config and then expects a different value
   later will get the first instance back. Existing tests work around this; follow their pattern
   rather than inventing a reset.

7. **Add tests** in `vintasend/tests/`. Cover at minimum: the default applies when nothing is set,
   and the environment variable overrides the framework value. Framework-specific paths are only
   testable when that framework is installed — none of the three are dependencies here, so guard or
   skip accordingly rather than adding a dependency.

8. **Document it in [`README.md`](../../../README.md)** if a consumer needs to set it. This is a
   library: an undocumented setting effectively does not exist for its users.

9. **Update the Environment Variables section of [AGENTS.md](../../AGENTS.md)** so the recognized
   list stays accurate.

## Non-string settings

`NOTIFICATION_DEFAULT_BCC_EMAILS` is typed `list[str]` and `NOTIFICATION_ADAPTERS` is
`list[tuple[str, str]]`, but `os.getenv` returns a string. Nothing in `app_settings.py` parses
them. So a list-typed setting works when supplied through a framework config object and produces a
raw string when supplied through the environment.

If you add a non-string setting, decide explicitly and say so in the docstring and README:

- Only supportable via framework config (document that the env var is not usable), or
- Parse it yourself at the assignment site in `__init__` (e.g. split a comma-separated string) and
  handle both the already-typed and the string case, since the value's origin is not knowable from
  the value alone.

Do not silently `cast` a string to `list[str]` and move on — that is a type lie that mypy cannot
catch and it will surface as an obscure failure inside an adapter.

## Naming

- Prefix every setting `NOTIFICATION_`. `get_config` looks the name up verbatim in the process
  environment, so an unprefixed name like `FROM_EMAIL` would collide with unrelated host-application
  variables.
- Use `SCREAMING_SNAKE_CASE`. The same string is the TypedDict key, the class attribute, the Django
  settings attribute, the Flask config key, the FastAPI config attribute, and the env var — there is
  exactly one spelling and it appears in all six places.

## Pitfalls

- **Adding to `DEFAULT_SETTINGS` but forgetting the `NotificationSettings.__init__` assignment.**
  The setting exists in the dict and is simply never read. Nothing errors; the feature just does not
  work.
- **Adding the `__init__` assignment but not the class annotation.** mypy will not flag the missing
  declaration on an instance attribute assigned in `__init__`, so this passes the gate and confuses
  the next reader.
- **Adding the key to the three framework dicts when the default does not differ.** Now four places
  must change together forever.
- **Testing only under one framework.** Django, Flask, and FastAPI are probed, not required. A
  setting can work under Django and be `{}` under a bare-Python host because of the `Unknown`
  branch.
- **Importing a framework at module scope to test the framework path.** Never do this — the probe
  functions import locally on purpose, and a module-scope import makes the framework a hard
  dependency.
- **Assuming a falsy framework value is honored.** `if framework_value` discards `False` / `0` /
  `""`.

## Verification

```bash
poetry run ruff check .
poetry run mypy
poetry run pytest
```

Then confirm by hand that the setting actually resolves:

```bash
NOTIFICATION_MY_NEW_SETTING=from-env poetry run python -c "
from vintasend.app_settings import NotificationSettings
print(NotificationSettings().NOTIFICATION_MY_NEW_SETTING)
"
```

It must print `from-env`. If it prints the default, the environment lookup is not wired — recheck
steps 5 and the exact spelling of the key. If it raises `AttributeError`, step 5 is missing
entirely.
