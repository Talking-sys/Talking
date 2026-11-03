# Talking game-embed SDK — Godot 4 GDScript sketch (not a shippable addon).
# Planned: HTTPRequest / HTTPClient against /api/sdk/v1. MIT (sdk/LICENSE).
# Load the API key from an environment file or export preset — never commit it.
# See docs/sdk/PLAN.md for the binding roadmap.
extends RefCounted
class_name TalkingClient

var server_url: String
var app_id: String
var timeout_sec: float = 15.0
var max_retries: int = 3
var offline: bool = false
var _api_key: String
var _target: String = ""

func _init(p_server_url: String, p_api_key: String, p_app_id: String = "") -> void:
	if p_server_url.is_empty() or p_api_key.is_empty():
		push_error("TalkingClient: server_url and api_key are required")
		return
	server_url = p_server_url.rstrip("/")
	_api_key = p_api_key
	app_id = p_app_id

func set_target_language(code: String) -> void:
	_target = code.strip_edges().to_lower()

func get_target_language() -> String:
	return _target

# Async-friendly: await these after wiring HTTPRequest in a Node.
func list_languages() -> Array:
	push_error("Godot binding is a skeleton — call GET /api/sdk/v1/languages (see docs/sdk).")
	return []

func translate_text(text: String, source: String = "auto", target: String = "", return_audio: bool = false) -> Dictionary:
	push_error("Godot binding is a skeleton — POST /api/sdk/v1/translate/text")
	return {}

func translate_audio(audio: PackedByteArray, format: String = "wav", target: String = "", return_audio: bool = false) -> Dictionary:
	push_error("Godot binding is a skeleton — POST /api/sdk/v1/translate/audio")
	return {}
