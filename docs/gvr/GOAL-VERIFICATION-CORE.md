# GVR Goal Verification Core

## Status

Experimental OSS core on `feature/gvr-goal-verification-core-v8`.

This module is intentionally isolated from source extraction, semantic search,
incident reasoning, runtime/deployment correlation, and LLM providers. Physical
placement in this repository is provisional; its public contracts are designed
to be split into a standalone OSS GVR package without changing semantics.

## Purpose

GVR answers a narrower question than an LLM critic:

> Given a structured goal, known state, proposed actions, preconditions,
> resources, effects, invariants, and evidence, does the proposal establish the
> required state without violating known constraints?

The core does **not** decide what a user meant. Natural-language interpretation
may propose structured objects, but interpretation is evidence/candidate input,
not a verification verdict.

## Fundamental semantics

Verdicts are:

- `PASS` — every selected deterministic verifier established its obligations;
- `FAIL` — at least one selected verifier established a contradiction or failed
  requirement;
- `UNKNOWN` — no selected verifier proved failure, but at least one required
  proposition/effect could not be established.

Aggregation is fail-closed:

```text
FAIL > UNKNOWN > PASS
```

`UNKNOWN` must never be treated as `PASS`.

A `PASS` is relative to the selected verifier set. An empty verifier registry
means only "no selected verifier failed". It does **not** mean a proposal is
safe. BugZero's closed verification policy decides which verifiers are mandatory
for a particular risk/action class.

## Structured model

The initial core exposes:

- `Predicate(subject, attribute, operator, value)`
- `Goal(id, conditions)`
- `Action(name, preconditions, effects, consumes)`
- `Proposal(actions, claims)`
- `Evidence(kind, reference, metadata)`
- `VerificationContext(state, goals, invariants, resources, evidence)`
- `VerificationIssue`
- `VerificationReport`
- `VerifierRegistry`

The initial deterministic verifiers are:

- preconditions;
- resource sufficiency;
- effect coverage;
- claim consistency;
- goal satisfaction;
- invariants.

## State transition rule

An action is not allowed to magically apply effects when its prerequisites do
not permit execution.

For each action:

1. evaluate preconditions against the current simulated state;
2. evaluate resource requirements against remaining resources;
3. if a prerequisite/resource is definitely `FAIL`, do not apply effects;
4. if a prerequisite/resource is `UNKNOWN`, mark affected post-state slots as
   `INDETERMINATE`;
5. only when gates pass, consume resources and apply supported deterministic
   effects;
6. when an effect itself cannot be simulated, mark only its affected post-state
   slot `INDETERMINATE`.

Any predicate evaluated against an indeterminate slot returns `UNKNOWN`, not a
comparison against stale pre-action state.

## Canonical example: car wash

Initial state:

```text
person.location = home
car.location = home
car.washed = false
```

Goal:

```text
car.location = car_wash
car.washed = true
```

Proposal:

```text
walk_to_car_wash
  effect: person.location = car_wash
```

Post-state:

```text
person.location = car_wash
car.location = home
car.washed = false
```

Result: `FAIL`.

The result does not depend on a textual heuristic such as "300 meters is close".
It follows from entity tracking and goal/postcondition mismatch.

A proposal that moves the car to the wash and then marks the car washed can
`PASS`, assuming all selected preconditions/invariants/resources also pass.

## Missing prerequisite example

If an action requires:

```text
ivan.email EXISTS
```

but the state contains no email fact, the precondition is `UNKNOWN`. GVR does
not fabricate an address and does not silently pass the action.

## Resource example

If:

```text
car.fuel_liters = 5
```

and the action requires 8 liters, the resource verifier returns `FAIL`.

Resource consumption is cumulative across a proposal.

## Counterexamples

`counterexamples(report)` produces minimal deterministic witnesses from failed
or unknown issues. It does not ask an LLM to invent a narrative.

For the walking/car-wash failure a witness contains the required car location,
the actual post-state car location, verifier/code, and verdict.

Future adversarial/LLM counterexample generation may propose additional claims,
but those claims must still pass through GVR evidence/verifier semantics.

## OSS vs closed boundary

OSS GVR Core owns reusable mechanisms:

```text
claims / structured propositions
state transition semantics
PASS | FAIL | UNKNOWN
verifier registry
precondition/postcondition checks
invariants
resource checks
contradiction checks
counterexample witnesses
claim/evidence/dependency primitives (future)
invalidation/re-verification primitives (future)
```

Closed BugZero policy owns decisions such as:

```text
which checks are mandatory
risk classification
required evidence combinations
production-action gates
incident-specific policies
runtime/deployment evidence arbitration
model arbitration/escalation
cost/latency budgets
autonomous remediation promotion rules
```

The core must not encode BugZero-specific product policy.

## Relationship to Source Evidence

The Graphify-derived source layer is an evidence producer.

For example:

```text
STATIC_AST/CROSS_FILE + EXACT + confidence=1.0
```

may be strong evidence for a GVR verifier, but it is not itself a GVR `PASS` or
`VERIFIED` state.

Semantic similarity is candidate discovery, never deterministic proof of a
source relation.

## Current non-goals

The initial core does not implement:

- natural-language goal extraction;
- an LLM critic;
- probabilistic world simulation;
- domain-specific causal models;
- claim dependency persistence;
- automatic invalidation/re-verification;
- temporal planning;
- external tool execution;
- BugZero production policy.

These can be added behind explicit contracts without weakening the core
PASS/FAIL/UNKNOWN semantics.
