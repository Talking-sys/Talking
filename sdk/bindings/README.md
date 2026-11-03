# Engine bindings (skeletons)

These files sketch the **same public API** as the Python and JavaScript clients so Unity, Unreal, and Godot studios can start wiring UI against a stable surface. They are **not** shippable plugins in this PR.

| Engine | Path | Status |
|---|---|---|
| Unity (C#) | `unity/TalkingClient.cs` | Interface + constructor checks; methods throw `NotImplementedException` |
| Unreal (C++) | `unreal/TalkingClient.h` | Header-only sketch using `FHttpModule` types |
| Godot 4 (GDScript) | `godot/talking_client.gd` | `RefCounted` class with the same method names |

Until a binding is implemented, engines should call the HTTP API directly (`docs/sdk/openapi-sdk-v1.yaml`) or embed the MIT JavaScript / Python client in a sidecar.

Roadmap, streaming notes, and open decisions: [`docs/sdk/PLAN.md`](../../docs/sdk/PLAN.md).
