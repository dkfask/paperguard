"""Conservative provider-reported token facts for the offline W-08 slice.

Not a billing ledger. Legacy character-derived estimates stay separate. Missing
or malformed usage is unavailable, never zero. Successful text plus unavailable
usage needs metering review, not an automatic supplier retry/refund.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re

from app.db import _now, new_id

KIND = "ai_usage_fact"
MAX_TOKENS = (1 << 63) - 1


@dataclass(frozen=True)
class ProviderUsage:
    protocol: str = "unavailable"
    usage_quality: str = "unavailable"
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    provider_request_ref: str | None = None


class CompletionText(str):
    """Keep string API consumers/3-argument test doubles backward compatible."""

    def __new__(cls, text: str, usage: ProviderUsage):
        instance = super().__new__(cls, text)
        instance.usage = usage
        return instance


def _count(value):
    # bool is an int subclass; floats/strings are not provider token measurements.
    return type(value) is int and 0 <= value <= MAX_TOKENS


def provider_usage(protocol: str, data: object) -> ProviderUsage:
    if protocol not in {"openai", "anthropic"}:
        return ProviderUsage()
    if not isinstance(data, dict):
        return ProviderUsage(protocol=protocol)
    provider_id = data.get("id")
    reference = None
    if isinstance(provider_id, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", provider_id):
        # Reference only: no raw provider payload/identifier enters audit/API.
        reference = hashlib.sha256((protocol + ":" + provider_id).encode()).hexdigest()
    unavailable = ProviderUsage(protocol=protocol, provider_request_ref=reference)
    raw = data.get("usage")
    if not isinstance(raw, dict):
        return unavailable
    input_key, output_key = ("prompt_tokens", "completion_tokens") if protocol == "openai" else ("input_tokens", "output_tokens")
    input_tokens, output_tokens = raw.get(input_key), raw.get(output_key)
    if not _count(input_tokens) or not _count(output_tokens):
        return unavailable
    total = raw.get("total_tokens")
    if "total_tokens" in raw and (not _count(total) or total != input_tokens + output_tokens):
        return unavailable
    cache_read = cache_creation = None
    if protocol == "anthropic":
        cache_read = raw.get("cache_read_input_tokens")
        cache_creation = raw.get("cache_creation_input_tokens")
        if any(key in raw and not _count(raw[key]) for key in ("cache_read_input_tokens", "cache_creation_input_tokens")):
            return unavailable
    elif "prompt_tokens_details" in raw:
        details = raw["prompt_tokens_details"]
        if not isinstance(details, dict):
            return unavailable
        cache_read = details.get("cached_tokens")
        if "cached_tokens" in details and (not _count(cache_read) or cache_read > input_tokens):
            return unavailable
    # Do not invent a total, aggregate Anthropic cache counters, derive prices,
    # or convert characters to provider-reported facts.
    return ProviderUsage(protocol, "provider_reported", input_tokens, output_tokens,
                         total, cache_read, cache_creation, reference)


def completion(text: str, *, protocol: str, data: object) -> CompletionText:
    return CompletionText(text, provider_usage(protocol, data))


def complete_request(store, request_id: str, result: str, *, expected_membership: dict | None,
                     input_estimate: int, output_estimate: int) -> None:
    """Atomically complete one dispatch + append its immutable usage fact/audit.

    Sidecar in the existing workflow_records table avoids production schema work.
    Scope comes only from the persisted request, never from response/client data.
    Any local persistence failure rolls back and the caller marks outcome unknown.
    """
    usage = result.usage if isinstance(result, CompletionText) else ProviderUsage()
    if not isinstance(usage, ProviderUsage) or usage.protocol not in {"openai", "anthropic", "unavailable"}:
        usage = ProviderUsage()
    elif usage.usage_quality != "provider_reported" or not _count(usage.input_tokens) or not _count(usage.output_tokens):
        usage = ProviderUsage(protocol=usage.protocol, provider_request_ref=usage.provider_request_ref)
    if usage.provider_request_ref is not None and not re.fullmatch(r"[a-f0-9]{64}", usage.provider_request_ref):
        usage = ProviderUsage(protocol=usage.protocol)
    now = _now()
    with store.workflow_transaction() as conn:
        request = conn.execute("SELECT * FROM ai_requests WHERE id=?", (request_id,)).fetchone()
        if not request or request["status"] != "dispatching":
            raise ValueError("current dispatch required")
        user = conn.execute("SELECT status,must_change_password FROM users WHERE id=?", (request["user_id"],)).fetchone()
        if not user or user["status"] != "active" or (request["idempotency_domain"] in {"tenant", "platform"} and user["must_change_password"]):
            raise PermissionError("account authorization changed")
        if request["tenant_id"] or request["membership_id"]:
            if not expected_membership:
                raise PermissionError("member snapshot required")
            member = conn.execute(
                "SELECT m.auth_version, m.capabilities FROM tenant_memberships m "
                "JOIN tenants t ON t.id=m.tenant_id "
                "WHERE m.id=? AND m.tenant_id=? AND m.user_id=? AND m.status='active' AND t.status='active'",
                (request["membership_id"], request["tenant_id"], request["user_id"]),
            ).fetchone()
            try:
                capabilities = json.loads(member["capabilities"] or "[]") if member else []
            except (TypeError, ValueError):
                capabilities = []
            if (
                not member
                or expected_membership["id"] != request["membership_id"]
                or member["auth_version"] != expected_membership["auth_version"]
                or int(request["auth_version"] or 0) != member["auth_version"]
                or "tenant:read" not in capabilities
            ):
                raise PermissionError("member authorization changed")
        elif expected_membership is not None:
            raise PermissionError("legacy request is not a tenant request")
        elif request["idempotency_domain"] == "platform":
            binding = conn.execute(
                "SELECT auth_version FROM platform_role_bindings "
                "WHERE user_id=? AND role_code='platform_admin' AND status='active'",
                (request["user_id"],),
            ).fetchone()
            if not binding or int(request["auth_version"] or 0) != int(binding["auth_version"]):
                raise PermissionError("platform authorization changed")
        reservation_row = conn.execute(
            "SELECT data FROM workflow_records WHERE kind='ai_quota_reservation' AND id=?",
            (request_id,),
        ).fetchone()
        if reservation_row:
            reservation = json.loads(reservation_row["data"])
            store._validate_ai_reservation_binding(dict(request), reservation)
            if reservation.get("state") != "dispatching":
                raise ValueError("AI reservation is not dispatching")
            reservation["state"] = "settled"
            reservation["revision"] = int(reservation.get("revision") or 0) + 1
            reservation["settled_at"] = now
            reservation["updated_at"] = now
            conn.execute(
                "UPDATE workflow_records SET data=?, updated_at=? WHERE kind='ai_quota_reservation' AND id=?",
                (json.dumps(reservation, ensure_ascii=False, separators=(",", ":")), now, request_id),
            )
        fact = {
            "id": request_id, "request_id": request_id, "tenant_id": request["tenant_id"],
            "membership_id": request["membership_id"], "user_id": request["user_id"],
            **asdict(usage), "metering_review_required": usage.usage_quality != "provider_reported",
            "billable": False, "created_at": now,
        }
        conn.execute("INSERT INTO workflow_records (kind,id,data,created_at,updated_at) VALUES (?,?,?,?,?)",
                     (KIND, request_id, json.dumps(fact, sort_keys=True), now, now))
        conn.execute("UPDATE ai_requests SET status='completed',reservation_status='settled',response_text=?,input_tokens=?,output_tokens=?,updated_at=? WHERE id=?",
                     (str(result), input_estimate, output_estimate, now, request_id))
        store.save_audit_event_conn(conn, {
            "id": new_id("audit"), "actor_id": request["user_id"], "action": "ai.usage.recorded",
            "resource_type": "saas", "resource_id": request_id,
            "reason": json.dumps({"tenant_id": request["tenant_id"], "membership_id": request["membership_id"],
                                  "usage_quality": usage.usage_quality, "billable": False}),
        })


def view_fields(store, request: dict) -> dict:
    """Caller must first fetch a scoped request; never query by raw client ID."""
    fallback = {"usage_quality": "legacy_chars" if request["status"] == "completed" else "unavailable", "provider_input_tokens": None,
                "provider_output_tokens": None, "provider_request_ref": None,
                "metering_review_required": True}
    fact = store.get_workflow_record(KIND, request["id"])
    if not fact or any(fact.get(key) != request[key] for key in ("tenant_id", "membership_id", "user_id")) or fact.get("request_id") != request["id"]:
        return fallback
    # Unknown/dispatching outcomes must not inherit a previous measurement grant.
    if request["status"] != "completed":
        return {**fallback, "usage_quality": "unavailable"}
    reported = fact.get("usage_quality") == "provider_reported" and _count(fact.get("input_tokens")) and _count(fact.get("output_tokens"))
    return {"usage_quality": "provider_reported" if reported else "unavailable",
            "provider_input_tokens": fact["input_tokens"] if reported else None,
            "provider_output_tokens": fact["output_tokens"] if reported else None,
            "provider_request_ref": fact.get("provider_request_ref") if isinstance(fact.get("provider_request_ref"), str) and re.fullmatch(r"[a-f0-9]{64}", fact["provider_request_ref"]) else None,
            "metering_review_required": not reported}
