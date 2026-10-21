# V3 coordinate and configuration contract

## One configuration, three consumers

`configs/environments/hospital_v3.json` is the runtime source for rendering, simulation and evaluation. The authoring recipe in `scripts/build_hospital_v3.py` regenerates this JSON; it is not imported by the application. The original `hospital_floor_01.json` is unchanged.

`Building` and `Floor` are in `backend/locus_x/building.py`. `Floor` **inherits the existing `Environment`**, including anchors, solid walls, rooms and waypoint routes. `BuildingRoom` only adds an ID to the existing room type. The frontend mirrors these contracts in `src/types.ts` and consumes the catalog over REST. `floorsOf()` supplies missing presentation metadata for legacy floors without replacing their geometry.

## Coordinates and units

| Field / space | Meaning |
| --- | --- |
| `position: [x, y]`, `a`, `b`, `center`, `entry` | Local floor coordinates in metres; +X east, +Y north |
| `width` | East–west extent in metres |
| `height` on environment/building/floor | North–south length, retained for V1 compatibility; **not vertical height** |
| `height` on wall/door | Physical vertical extent in metres |
| `size: [width, length]` | Axis-aligned room/region footprint |
| `floor.elevation` | Known floor datum above building ground, in metres |
| `Frame.floor_elevation` | Authoritative selected floor datum; no estimation claim |
| Three.js mapping | `(x, y) → (x, elevation, -y)`; one metre is one scene unit |
| Exploded placement | Add `index × EXPLODE_GAP` to the configured elevation **only for display** |
| Covariance | 2 × 2 covariance in the backend X/Y plane, units m² |
| Ellipse | Semimajor/minor in metres; angle measured in backend X/Y radians; rotate before mapping to X/−Z |
| Timestamps | Simulation seconds; HTTP/render time does not determine the target position |

All floors use the same local origin. No floor-dependent X/Y rotation or translation is applied. Scene geometry, observations, paths and ellipses are children of the same floor transform. Unavailable fixes do not become zero coordinates, and trajectories break across missing estimates and floor changes. Estimates outside the footprint are not clamped.

The current estimator still consumes **two-dimensional ranges and anchor coordinates**. Rendered anchor mast height and furniture are presentation only. The source of floor identity is manual selection, not a floor classifier, altimeter or vertical solve. Cross-floor radio propagation and motion transitions are not implemented.

## Building schema

| Root fields | Contract |
| --- | --- |
| `schema_version: "3.0"`, `version` | Format discriminator and configuration revision (`3.0.0` for this fixture) |
| `id`, `name`, `description` | Stable environment ID and human-readable description |
| `width`, `height` | Building footprint; every floor must fit inside it |
| `default_floor_id` | Existing floor selected when omitted at session creation |
| `floors` | Ordered, validated `Floor` objects with strictly increasing elevations |
| `connections` | Named stairs/elevator connections and `{floor_id, access_id}` stops |
| `validation` | Metre units, fictional/procedural provenance, geometry revision and assumptions |

This fixture is **Meridian Hospital**, fictional, 42 × 28 m per floor. Ground is 0 m, First 4.2 m and Second 8.4 m. Walls are 3.2 m tall. Rooms/circulation are plausible experiment architecture, not a code-compliant clinical design or a reproduction of a real hospital.

Each floor retains the complete V1 geometry fields and adds:

- `short_name`, `elevation`, `color`: presentation identity and physical datum.
- `rooms`: semantic room rectangles with stable floor-local IDs. Equipment is generated from room semantics for inspection; it is not an obstruction model.
- `corridors`: named rectangular circulation regions.
- `doors`: opening endpoints, physical height and two connected region IDs. `outside` is reserved for perimeter entrances.
- `access_cores`: aligned stair/lift footprints and entry points. The supplied cores have south-facing entries; the architectural fixture renderer is designed for this arrangement.
- `movement_regions`: references to room/corridor IDs. This avoids duplicating walkable geometry. Shafts are not walkable route regions.
- `route_names`: a display name for every key in the existing waypoint `routes` map. Routes are closed piecewise-linear loops with dwell times, including the last-to-first segment.
- `anchors`, `anchor_layout_id`: the **installed** layout used for measurements. IDs A1–A8 are scoped to each floor in this protocol.
- `candidate_anchors`: independent, floor-local sites C01–C21. They produce no observations and have no effect on estimation. No recommendation, ranking or mounting feasibility is implied.

Canonical sample references: `hospital_v3 / first / A1`, `hospital_v3 / second / theatre-01`. An anchor ID alone is meaningful only inside a selected floor/run.

## Validation and propagation

Startup validates bounds, finite values, unique IDs, positive region sizes, non-overlapping architectural regions, nonzero walls, candidate bounds, route display names, movement references, door connections, jamb endpoints and wall-free opening interiors. Every **complete route segment** must lie in the union of valid movement rectangles and must not intersect any solid wall. Rectangle interval coverage is exact rather than waypoint-only sampling.

Building validation checks floor IDs/elevations, slab/wall clearance, aligned core footprints, matching connection kinds, valid stops and exactly one connection per access core. The current contract describes vertical circulation; it does not simulate traversal.

Doors are already absent from `walls`: the simulator intersects only the solid segments. Door metadata makes those same gaps visible and checkable. Cutaway walls (0.62 m exterior / 1.05 m interior) and hidden layers do not change propagation. Full-height walls use configured heights. Only the **Wall range bias** experiment control changes whether wall intersections inject bias.

## API additions and compatibility

The `/api/v1` namespace and original endpoints remain. An empty `POST /sessions` still creates `hospital_floor_01`. The browser explicitly requests `hospital_v3` for a new V3 session.

```json
{
  "environment_id": "hospital_v3",
  "floor_id": "first",
  "settings": { "scenario": "noise", "seed": 12, "route": "asset_round" }
}
```

`GET /environments` returns a union of legacy floors and versioned buildings. Every frame adds `environment_id`, `environment_version`, `environment_sha256`, `floor_id`, `floor_elevation` and `localization_mode: "2d_on_known_floor"`. The frontend refuses to combine a frame with a mismatched environment or floor elevation.

`POST /sessions/{id}/floor` accepts `{ "expected_revision": 7, "floor_id": "second" }`. A different valid floor preserves applicable scenario/seed/speed settings, filters unknown disabled-anchor IDs, chooses an available route if necessary, resets time/filter/trust and increments revision. Selecting the current floor is an idempotent inspection action. Invalid floor requests return 422 and stale requests 409, without changing the session.

Reset restores the **creation floor and creation settings**. A floor switch is not evidence of a modeled transition. Browser mutations, reconnects and environment replacements share one serial queue. The UI selects a floor only after backend acknowledgement; replacing an environment releases only the previous ephemeral session after the new session succeeds.

## Metrics and reproducibility

The live window includes at most 600 actual samples and clears when the session, floor or settings change. Same-timestamp recomputations replace a sample. Fault edits retain tracker history; these window metrics are diagnostics, not independent benchmark runs.

RMSE, median and P95 use available errors. Valid rate counts available fixes / all frames. Fresh-update rate counts accepted updates / all frames. Prediction-only fixes count as valid but not fresh. Latency is the mean measured algorithm runtime, including failed attempts. Coverage is the fraction of available LOCUS-X ellipses containing simulator truth; missing ellipses are excluded from that denominator. Baseline coverage is not evaluated and displays N/A.

Benchmark requests accept `environment_id` and `floor_id`; all methods still receive identical observations. V3 results include `result_version`, environment version, floor ID, localization mode and the **whole-building canonical configuration hash**. Legacy serialization and the reference result shape/hash remain unchanged. Accuracy and traces reproduce; wall-clock latency does not.

The benchmark CLI creates unique output directories by default and refuses to overwrite existing `results.json` or `metrics.csv`. Keep configuration changes and algorithm changes in new result versions. Never relabel old reference results as V3 results.
