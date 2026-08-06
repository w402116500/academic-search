# Frontend Component Guidelines

## Vue Composition

Vue components use `<script setup lang="ts">` with imported feature helpers,
route composition APIs, `ref` for local mutable state, and `computed` for
derived state. Page-level views coordinate feature hooks rather than keeping
API query implementation inline.

References: `frontend/src/views/AuthView.vue`,
`frontend/src/views/PlanReviewView.vue`,
`frontend/src/views/ResearchChatView.vue`.

## Forms And Validation

Use native form submission with `@submit.prevent`, bind inputs with `v-model`,
and keep form-specific errors local to the view. The authentication form uses
native `type`, `required`, `autocomplete`, and `minlength` constraints; its
submission error is exposed with `role="alert"`.

References: `frontend/src/views/AuthView.vue`,
`frontend/tests/e2e/auth-flow.spec.ts`.

When a page has domain validation that must be independently tested, place it
in a typed feature helper. `buildResearchScope` converts view input to the API
scope contract and is covered by a focused Vitest suite.

References: `frontend/src/features/research/scope.ts`,
`frontend/tests/unit/research-scope.test.ts`.

## Accessibility Evidence

Current interactive views use labelled controls and explicit ARIA roles or
states for non-native controls, live task status, dialogs, and alert messages.
Browser tests select authentication fields by their accessible labels and
buttons by role and name.

References: `frontend/src/views/AuthView.vue`,
`frontend/src/views/PlanReviewView.vue`,
`frontend/src/views/VerificationTaskView.vue`,
`frontend/tests/e2e/auth-flow.spec.ts`.

## Styling

Stylesheets are imported from the application entrypoint: `src/styles.css` is
the global base, reusable shared styles live under `src/assets/styles/`, and
page or feature styles live beside their feature. The auth view is styled by
`features/auth/auth.css` imported in `main.ts`.

References: `frontend/src/main.ts`, `frontend/src/features/auth/auth.css`.
