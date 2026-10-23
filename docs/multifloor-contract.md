# Multi-floor integration contract

Status: Task 7, API v3.0.0. This document is the integration specification for the
multi-floor session that the backend exposes additively on top of the existing
single-floor API. Nothing here changes a legacy request or response.

The only normative sources are the FastAPI response models in
`backend/locus_x/schemas.py` and the implementation in
`backend/locus_x/multifloor/session.py`; this document explains their meaning.

## 1. Two session types, one API

`POST /api/v1/sessions` creates either session from the same endpoint. Omitting
`multifloor` creates the existing `SimulationSession` with exactly the existing
payload. Supplying it creates a `MultiFloorSession`:

```json
{
  "environment_id": "hospital_v3",
  "settings": { "scenario": "nlos", "route": "ascent_stairs" },
  "multifloor": { "mode": "inferred", "route_id": "ascent_stairs" }
}
```

`multifloor` fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `mode` | `"inferred"` or `"route_assisted"` | How the storey may be identified. Required. |
| `route_id` | string | Id of one of the building's `transition_routes`. Required. |
| `device_height` | number in `(0, 4)` metres, optional | Tracker height above the storey. Defaults to `building.target_device_height`. |

Rules enforced at creation:

* the environment must be a `Building` (`schema_version: "3.0"` with `floors`);
* `floor_id` must be omitted or `null` — a multi-floor storey is never selected;
* `mode` must be one of the two literals and `route_id` must exist;
* `settings.route` is overwritten with `route_id`, because for a multi-floor
  session the route *is* the simulated trajectory.

Refusals are `422` with a human-readable `detail`. Unknown `environment_id` is
`404` as before.

The existing endpoints drive both session types unchanged:

| Endpoint | Multi-floor behaviour |
| --- | --- |
| `GET /api/v1/sessions/{id}` | Latest frame. |
| `POST /api/v1/sessions/{id}/advance` | Advance time and recompute. |
| `PATCH /api/v1/sessions/{id}/controls` | Edit settings; see §6. |
| `POST /api/v1/sessions/{id}/reset` | Restore the creation-time mode, route, device height and settings, then restart at t=0. |
| `DELETE /api/v1/sessions/{id}` | Delete. |
| `POST /api/v1/sessions/{id}/floor` | Always `409`. The storey is derived, not selected. |

`expected_revision` is authoritative: a stale revision returns `409` before any
state change, for both session types. A failed mutation leaves the revision and
frame untouched.

## 2. Modes and what their floor identity means

`Frame.localization_mode` is one of:

| Value | Meaning |
| --- | --- |
| `"2d_on_known_floor"` | Legacy session. `floor_id` and `floor_elevation` are the configured floor; no 3D fields. |
| `"multifloor_inferred"` | Storey comes only from 3D ranges. `FloorState.source` is `"inferred"`. |
| `"multifloor_route_assisted"` | Storey comes from the configured route witness while it is known. `FloorState.source` is `"route_assisted"`. |

A route-assisted floor is **not** an inference result. It is configuration: the
simulator's route declares which storey the target is on at that instant, and the
tracker consumes that declaration. It is auditable and it is never presented as a
range-based estimate or as a confidence. Hypothesis probabilities are still
computed from ranges over **every** declared storey in both modes; route
assistance never narrows, weights or edits them.

In `inferred` mode the tracker is structurally denied route assistance. A
transition in that mode therefore carries no transition, connection or floor ids
(§5), because no route witness was consulted.

## 3. Frame fields

All units are metres and seconds. Positions are `(x, y)` in the building's
horizontal plane. Angles are radians in that plane.

| Field | Type | Presence | Meaning |
| --- | --- | --- | --- |
| `session_id` | string | always | Session handle. |
| `environment_id` | string | always | Environment/building id. |
| `environment_version` | string | always | Configuration version. |
| `environment_sha256` | string | always | SHA-256 of the validated configuration's canonical JSON. |
| `floor_id` | string or `null` | always | Reported storey. `null` only for an ambiguous multi-floor frame. |
| `floor_elevation` | number or `null` | always | Configured floor-plane elevation of `floor_id`. `null` only when `floor_id` is `null`. Never an estimate. |
| `localization_mode` | literal | always | §2. |
| `revision` | integer | always | Increments on every accepted mutation. |
| `frame_index` | integer | always | Increments on `advance`; `0` after creation/restart. |
| `timestamp` | number | always | Simulated seconds; `0` after creation/restart. |
| `settings` | object | always | Effective simulator settings (§6). |
| `anchors` | array | always | Anchor list; ordering in §4. |
| `measurements` | array | always | Available range observations only; ordering in §4. |
| `simulator_labels` | array | always | One label per entry in `anchors`, including unavailable anchors; ordering in §4. |
| `baseline` | `Estimate` | always | Legacy 2D baseline. Multi-floor: `status = "not_applicable"`, `position = null`, reason set. |
| `robust` | `Estimate` | always | Multi-floor: the 3D solve's horizontal projection and its 2×2 block of the 3D covariance. |
| `tracking` | object | always | Multi-floor: the tracker's horizontal state. Elevation is separate. |
| `evaluation` | object | always | Truth-based scoring plus truth elevation (§5). |
| `events` | array | always | Last 20 engine events. |
| `floor_state` | object or `null` | always; `null` for legacy | §5. |
| `transition` | object or `null` | always; `null` for legacy or outside a traverse | §5. |
| `elevation_estimate` | number or `null` | always; `null` for legacy | Tracker's absolute device elevation `z`. |
| `floor_probabilities` | object or `null` | always; `null` for legacy | Storey id → finite softmax probability over every declared storey. |

### Elevation semantics

`z` is **absolute device elevation above the building datum**, not a height above
the reported floor. Implication for drawing:

* truth marker: `toScene(ground_truth, evaluation.ground_truth_elevation)`;
* estimate marker: `toScene(tracking.position, elevation_estimate)`;
* a floor's configured plane is `floor.elevation`; people/devices on it start at
  `floor.elevation + device_height` (default `building.target_device_height`);
* the scene mapping is `(x, y, z) → (x, z, -y)` (Three.js uses elevation as `Y`).

`floor_elevation` tells you which plane the reported storey occupies. It is not
where the device is. `elevation_estimate` is where the device is believed to be.

### Legacy compatibility

For a legacy session all keys in the table above that predate multi-floor keep
their names, types and values. The additive fields `floor_state`, `transition`,
`elevation_estimate`, `floor_probabilities` and
`evaluation.ground_truth_elevation` are present as `null`. `floor_id` and
`floor_elevation` are never `null` when `localization_mode` is
`"2d_on_known_floor"`. A client that ignores unknown fields needs no change.

The contract-test reference values are:

* `hospital_floor_01` configuration hash:
  `fa1d5b056ee2b775d1f65fba958431332e4557f224ca2dbccca119fa760c4b39`
  (SHA-256 of the validated model JSON; the file itself is unchanged);
* legacy `floor_id = "hospital_floor_01"`, `floor_elevation = 0.0`;
* legacy frame key set = pre-existing keys ∪ the four additive `Frame` keys.

## 4. Anchor, measurement and label ordering

For a legacy session, `anchors` is the floor's anchor list in declaration order
and all three arrays describe that one floor.

For a multi-floor frame:

1. `anchors` lists **every installed anchor in the building**, floor by floor in
   `floors` declaration order, and within each floor in `anchors` declaration
   order. The same `id` (for example `"A1"`) therefore appears once per storey.
2. `simulator_labels[i]` belongs to `anchors[i]`; it includes every anchor, with
   `available: false` for disabled, timed-out or lost anchors.
3. `measurements` contains only anchors that produced a range, in the same order.
   Equivalently: the *k*-th measurement is the *k*-th label with
   `available: true`. `measurement.anchor_position` is the anchor's `(x, y)`;
   the storey comes from its position in `anchors`.

`settings.disabled_anchors` keeps its legacy meaning: a bare id disables that id
on **every** storey. `settings.disabled_anchor_keys` (`"<floor_id>:<anchor_id>"`)
disables one installed anchor on one storey and is only accepted for a building.

## 5. Multi-floor state blocks

### `floor_state`

```json
{ "floor_id": "first", "source": "inferred", "confidence": 0.94, "ambiguous": false }
```

| Field | Meaning |
| --- | --- |
| `floor_id` | Reported storey, or `null` while ambiguous. |
| `source` | `"inferred"`, `"route_assisted"` or `"configured"`. `"configured"` appears only when no decision/assist was supplied. |
| `confidence` | The winning storey's range likelihood, only when `source = "inferred"` and the decision is confident. Otherwise `null`; absence is intentional, not zero. |
| `ambiguous` | `true` when no storey is claimed, or when an anti-jump check rejected the hypothesis. |

An ambiguous frame has `floor_state.floor_id = null`, `floor_id = null`,
`floor_elevation = null`, and `confidence = null`. The UI must say "floor
ambiguous"; it must not substitute the previous floor or `default_floor_id`.

A storey change rejected as inconsistent with the configured vertical speed is
also reported as ambiguous; the last established storey is retained internally
for the next continuity check but is not published as the current floor.

### `transition`

```json
{
  "phase": "traversing",
  "transition_id": "ascent-stairs-up",
  "connection_id": "west-stairs",
  "from_floor_id": "ground",
  "to_floor_id": "first",
  "progress": 0.42
}
```

`phase` is `"entering"`, `"traversing"`, `"exiting"` or `"reacquiring"`.
`progress` is the fraction through the vertical traverse in `[0, 1]` when known.

In `route_assisted` mode the ids and progress come from the route witness. In
`inferred` mode `transition_id`, `connection_id`, `from_floor_id`,
`to_floor_id` and `progress` are all `null`: the tracker may know it is moving
vertically, but no route truth is available to name the connector.

### `evaluation`

Multi-floor frames keep the legacy scalar fields and add truth elevation:

| Field | Multi-floor meaning |
| --- | --- |
| `ground_truth` | Simulator's horizontal `(x, y)`. |
| `ground_truth_elevation` | Simulator's absolute device elevation `z`. Legacy: `null`. |
| `baseline_error` | `null`; the 2D baseline is not run. |
| `proposed_error` | Horizontal error of the tracker. |
| `covered_95` | Horizontal 95% coverage of the tracker ellipse. No vertical coverage is claimed. |
| `has_nlos`, `has_dropout` | Fault flags derived from simulator labels. |

Truth appears only in `evaluation` and `simulator_labels`. `Measurement`
contains `anchor_id`, `anchor_position`, `timestamp` and `observed_range` only;
it never carries truth, a floor id or a fault flag. The single sanctioned
truth → tracker path is the route-assisted storey hint described in §2.

## 6. Controls and restart semantics

`PATCH /controls` accepts the existing `Controls` model. For a multi-floor
session:

| Edit | Effect |
| --- | --- |
| `route` | Must be a `transition_routes` id. Restarts at `timestamp = 0`, `frame_index = 0`, new revision. |
| `speed`, `seed` | Restart, because they define the truth trajectory. |
| `scenario` | Applies the preset values and recomputes the current instant. |
| `noise_std`, `nlos_bias`, `dropout`, `walls_enabled`, `disabled_anchors`, `disabled_anchor_keys` | Recompute the current instant without restarting. |
| `expected_revision` | Required; stale values are `409`. |

A recompute restores the filter/trust state to what it was before the current
instant, so the same measurement is not applied twice. `reset` restores the
mode, route, device height and settings captured at creation, then restarts.

## 7. Honest 2D fields

A multi-floor frame never manufactures a 2D result:

* `baseline.status = "not_applicable"`, `baseline.position = null`, a reason is
  set and `runtime_ms = 0`;
* `robust.position` is the horizontal projection of the 3D solve;
  `robust.covariance` is its 2×2 horizontal block; residuals and reliability are
  keyed by observation key `"<floor_id>:<anchor_id>"` (not by bare anchor id);
* `tracking.position`, `velocity`, `covariance` and `ellipse` are horizontal;
* `elevation_estimate` is the vertical state;
* `evaluation.proposed_error` and `covered_95` are horizontal metrics.

Consumers that label `baseline` as "the 2D estimator" must handle
`status = "not_applicable"` and a `null` position rather than drawing a point at
zero.

## 8. Errors and status codes

| Code | Cause |
| --- | --- |
| `404` | Unknown environment or session id. |
| `409` | Stale `expected_revision`; `/floor` called on a multi-floor session. |
| `422` | Invalid mode/route/device height/settings, `floor_id` supplied with `multifloor`, non-building environment, unknown or duplicate anchor keys. |
| `429` | Session store capacity reached. |

## 9. Frontend integration notes

* TypeScript: `floor_id`, `floor_elevation`, `floor_state`, `transition`,
  `elevation_estimate`, `floor_probabilities` and
  `evaluation.ground_truth_elevation` are nullable. Do not use `!`.
* `floor_state.source` drives the badge: `configured` / `route assisted` /
  `inferred`. Show `confidence` only when it is non-null.
* Draw the target at `elevation_estimate` and the truth at
  `ground_truth_elevation` in building/exploded views. Do not place them on a
  selected floor plane.
* Keep anchor meshes on their configured floor; `frame.anchors` repeats ids, so
  look up labels by index/offset, not by `anchor_id` alone.
* While `transition` is non-null, show the phase and connector name. In
  `inferred` mode show that no route witness exists rather than inventing ids.
* The floor selector must be disabled for multi-floor sessions; `/floor` returns
  `409` by design.

## 10. Contract tests

`backend/tests/test_multifloor_api.py` covers both session types:
create/advance/controls/reset/delete, stale `409`, invalid mode/route/`floor_id`
`422`, `/floor` `409`, legacy key set and additive nulls, route-assisted source
versus inference, transition witnesses, and the pinned legacy environment hash.
`src/workspace.test.tsx` covers source badges, confidence, ambiguity rendering,
transition labels and elevation-aware trails.
