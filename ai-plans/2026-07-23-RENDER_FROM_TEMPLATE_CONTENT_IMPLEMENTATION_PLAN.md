# Render From Template Content — Implementation Plan

Ports `vintasend-ts`'s `renderEmailTemplateFromContent` (its v0.7.1 feature): re-render an email
notification from supplied template *content* rather than from the stored template reference, so a
caller can preview or reconstruct an old notification using the template as it was at the time.
Baseline is `vintasend` 2.0.0; `resend_notification` already shipped with the filtering work.

## 1. Goals

1. Add `render_from_template_content(notification, template_content, context)` to the email renderer
   seam, rendering subject and body from an inline template string instead of the notification's
   stored `body_template` / `subject_template` reference.
2. Expose it on both services as `render_email_template_from_content(...)`, returning the rendered
   `TemplatedEmail` without sending or persisting anything.

Non-goals:

- **No sending.** This renders and returns; it never delivers or writes. It is a read-shaped preview
  operation.
- **No SMS or non-email renderers.** TS scopes this to email (subject + body). The base renderer and
  the SMS renderer are untouched.
- **No template storage or versioning.** The caller supplies the historical content; this library
  does not store past template versions. Where the content comes from is the host's problem.
- **No context re-derivation.** The caller passes the context to render with — typically a
  notification's stored `context_used`. This method does not regenerate context from `context_name`.
- **No `vintasend-sqlalchemy` work.** It ships no renderer. Downstream scope is core +
  `vintasend-django`, plus `vintasend-jinja` as the other email-renderer package.

## 2. Guiding Decisions

| Decision | Resolution |
|---|---|
| **On `BaseTemplatedEmailRenderer`, not the base renderer** | TS places it on the email renderer because it is inherently about an email's subject and body. The base [BaseNotificationTemplateRenderer](../vintasend/services/notification_template_renderers/base.py) has a single `render` method returning an opaque `NotificationSendInput`; there is no meaningful "from content" for a renderer whose output shape is unknown. Scoping to `BaseTemplatedEmailRenderer` (whose `render` returns `TemplatedEmail`) keeps the signature concrete and honest. |
| **Abstract, not concrete-default** | A concrete default would have to either raise `NotImplementedError` (a landmine) or fall back to `render` while ignoring the supplied content (silently wrong — it would render the *current* template, defeating the entire purpose). Neither is acceptable, so the method is abstract. Per `AGENTS.md`, that is a minor bump with a mandatory `### Backwards compatibility` note; the two email-renderer packages (`vintasend-django`, `vintasend-jinja`) implement it in lockstep. |
| **Signature mirrors `render`** | `render_from_template_content(self, notification, template_content, context, **kwargs) -> TemplatedEmail`. Same notification and context parameters as `render`, with `template_content` replacing the stored reference. Keeping the shape parallel means an implementer writes it by copying `render` and swapping the template source. |
| **`template_content` carries subject and body together** | An email needs both. Rather than two positional strings, `template_content` is a small structured input (a dataclass or the renderer's natural template-pair shape) so subject and body travel as one argument — matching how `TemplatedEmail` already pairs them on output. Confirm the exact shape against how `vintasend-jinja` and `vintasend-django` currently locate their two templates. |
| **Service method renders through the configured renderer, does not pick one** | `render_email_template_from_content` finds the email adapter for the notification's type, reaches its template renderer, and calls the new method — the same indirection `send` uses to reach a renderer. It performs no I/O of its own. |
| **Renders with the supplied context verbatim** | No `get_notification_context` call. The whole point is reproducing a past render, and the caller holds the historical context (usually `notification.context_used`). Regenerating would produce a *current* render, not a historical one. |
| **No feature flag** | Additive read-only surface; a new abstract renderer method gated by the release. No flag module exists and none is warranted. |

## 3. Data Model Changes

No dataclass or stored-model changes. The one new type is the template-content input.

### 3.1 Template-content input

A structured pair carrying the historical templates, in
[base_templated_email_renderer.py](../vintasend/services/notification_template_renderers/base_templated_email_renderer.py)
alongside `TemplatedEmail`:

```python
@dataclass
class EmailTemplateContent:
    subject_template: str
    body_template: str
    preheader_template: str | None = None   # preheader is a Python-only concept; keep it optional
```

`preheader_template` has no TS counterpart but is a first-class field on the Python notification
dataclasses; including it optionally lets a full historical render reproduce the preheader too.

### 3.2 New renderer method

```python
# BaseTemplatedEmailRenderer
@abstractmethod
def render_from_template_content(
    self,
    notification: "Notification | OneOffNotification",
    template_content: EmailTemplateContent,
    context: "NotificationContextDict",
    **kwargs,
) -> TemplatedEmail: ...
```

## 4. Phased Rollout

Single concern, but split so the seam addition and the Django implementation are separate PRs.

### Phase 1 — Renderer seam method and service wiring

**Goal**: given an email notification, a historical template pair and a context, the service returns
the rendered subject and body without sending or persisting.

**Feature flag**: none — additive read-only surface, gated by the release.

Changes:

1. [base_templated_email_renderer.py](../vintasend/services/notification_template_renderers/base_templated_email_renderer.py):
   add `EmailTemplateContent` and the abstract `render_from_template_content`.
2. [fake_templated_email_renderer.py](../vintasend/services/notification_template_renderers/stubs/fake_templated_email_renderer.py):
   implement it — render the supplied content, not the stored reference. Complete, never raising, per
   the stubs rule. Mirror in any asyncio fake renderer if one exists.
3. [notification_service.py](../vintasend/services/notification_service.py): add
   `render_email_template_from_content(notification, template_content, context)` to
   `NotificationService` (near the other read-shaped methods, e.g. after `resend_notification` at
   `:946`) and to `AsyncIONotificationService` (after `:1969`). Each locates the email adapter for
   the notification type, reaches its renderer, verifies it is a `BaseTemplatedEmailRenderer`, and
   delegates. Raise a typed error when no email renderer is available for the type.
4. [exceptions.py](../vintasend/exceptions.py): `NotificationRenderError` (or reuse the existing
   `NotificationTemplateRenderingError` family if it fits — confirm in review).

Spec use-case: no spec — ports `renderEmailTemplateFromContent` from `vintasend-ts` v0.7.1.

Tests:

- **Unit**: `@vintasend/tests/test_services/test_render_from_content.py` — new. Rendering supplied
  content produces the expected subject/body; it renders the *supplied* content, not the
  notification's stored templates (assert they differ and the output tracks the argument); the
  preheader renders when provided and is omitted when not; a notification type with no email renderer
  raises. Both service classes.
- **Integration**: reconstruct a "past" render by passing a notification's `context_used` plus an
  older template pair, and assert the result matches what that pair would have produced — the audit
  use case end to end.

**Suggested AI model**: Tier 2 (IDs in [resources/ai-models.yaml](../.claude/skills/plan-feature/resources/ai-models.yaml)).
One renderer method plus symmetric service wiring, exact precedent in the existing `render` path and
in the TS implementation.

Acceptance: `render_email_template_from_content(notification, content, context)` returns a
`TemplatedEmail` rendered from `content` (not the stored templates), on both services, and raises when
the notification type has no email renderer — with nothing sent or persisted.

### Phase 1b — `vintasend-django` and `vintasend-jinja` implementations (parallel track, separate repos)

**Goal**: the two shipped email renderers implement the new method. Each runs alongside Phase 1;
**neither can merge until core has released**.

**Feature flag**: none.

Changes (each repo):

1. Implement `render_from_template_content` by rendering the supplied `EmailTemplateContent` through
   the same engine `render` uses — Django's template engine for `vintasend-django`, Jinja2 for
   `vintasend-jinja` — but from an inline string source rather than a template name/loader lookup.
2. Tests: rendering from content matches rendering the same content stored as a template; the
   preheader path; parity with the renderer's existing `render` output for identical input.

Spec use-case: no spec — downstream adoption.

**Suggested AI model**: Tier 2. Each is a single method rendering an inline string through an
existing engine.

Acceptance: both `vintasend-django` and `vintasend-jinja` email renderers implement
`render_from_template_content`, and rendering an inline template pair matches the stored-template
result for the same input.

## 5. Risk & Rollout Notes

- **One new abstract method breaks the email-renderer packages at instantiation** until they
  implement it. Minor bump with the mandatory note; core releases first, then `vintasend-django` and
  `vintasend-jinja`. Backends and non-email renderers are untouched, so `vintasend-sqlalchemy`,
  `vintasend-celery`, `vintasend-flask-mail` and `vintasend-fastapi-mail` need no release.
- **No migration, no persisted state, no backfill.** The method reads and returns.
- **Rollback**: Phase 1 is revertible before the renderer packages ship against the new method.
- **Injection-safety note**: rendering an inline template string is an injection surface if the
  template content is attacker-controlled. In the intended audit/preview use case the content comes
  from the application's own template history, not from user input — say so in the README so nobody
  wires user-supplied template strings straight in. Jinja2's autoescape defaults still apply.

## 6. Open Questions

| Question | Recommended default |
|---|---|
| Should the method also exist for SMS / other renderers? | **No, email only, matching TS.** The base renderer's output is opaque, so there is nothing concrete to render "from content". If an SMS preview need appears, add it to `BaseTemplatedSMSRenderer` then, with its own text-only content shape. |
| One `EmailTemplateContent` argument, or separate subject/body params? | **One structured argument.** An email is a subject+body (+preheader) unit, and a single argument keeps the signature stable if the shape grows. It also mirrors `TemplatedEmail` on the output side. |
| Reuse `NotificationTemplateRenderingError` or add a new exception? | **Reuse the existing family if the failure is a render failure; add `NotificationRenderError` only for the "no email renderer configured" case.** Confirm in review against how `render` failures are currently surfaced. |
| Should the service regenerate context if the caller passes none? | **No.** Requiring the caller to pass context keeps the method honest about being a *reproduction*. A convenience overload that pulls `context_used` can be added later if asked for. |

## 7. Touch List

**Phase 1**

- [base_templated_email_renderer.py](../vintasend/services/notification_template_renderers/base_templated_email_renderer.py) — `EmailTemplateContent` + abstract method.
- [fake_templated_email_renderer.py](../vintasend/services/notification_template_renderers/stubs/fake_templated_email_renderer.py) — implementation.
- [notification_service.py](../vintasend/services/notification_service.py) — after `:946` and after `:1969`.
- [exceptions.py](../vintasend/exceptions.py) — possibly one new exception.
- `@vintasend/tests/test_services/test_render_from_content.py` — new.
- [RELEASE_NOTES.md](../RELEASE_NOTES.md) — minor entry with `### Backwards compatibility`.
- [README.md](../README.md) — a "Rendering a notification from historical template content" section, including the injection caveat.
- [pyproject.toml](../pyproject.toml) — version bump.

**Phase 1b (cross-repo — separate PRs, after core releases)**

- `vintasend-django`: email renderer + tests; widen the `vintasend` pin.
- `vintasend-jinja`: email renderer + tests; widen the `vintasend` pin.
