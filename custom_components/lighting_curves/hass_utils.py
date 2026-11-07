"""Service hook adapted from Adaptive Lighting (https://github.com/basnijholt/adaptive-lighting).

Apache-2.0, copyright its authors/contributors. Modified for Lighting Curves:
setup_service_call_interceptor and target_entities only; safe chained removal.
acted_on and the interceptor's after-callback are Lighting Curves code.
"""
from __future__ import annotations

import inspect
import logging

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.helpers import target as target_helpers
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.util.read_only_dict import ReadOnlyDict

_LOGGER = logging.getLogger(__name__)


def target_entities(hass, data, expand_group=False):
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(data), expand_group=expand_group)
    return selected.referenced | selected.indirectly_referenced


async def acted_on(hass, call, data):
    """The lights HA will act on for this call, or None if HA will reject it.

    Mirrors entity_service_call: "all" drops excluded lights and, for a non-admin, lights they
    may not control; any other target is rejected whole if one referenced entity is denied.
    """
    if data.get("entity_id") == "all":
        # ponytail: HA before 2026.11 has no exclude block, so nothing to drop there.
        excluded = getattr(target_helpers, "async_extract_excluded_entity_ids", lambda *_: set())(
            hass, TargetSelection(data))
        entities, whole = [e for e in hass.states.async_entity_ids("light") if e not in excluded], False
    else:
        entities, whole = target_entities(hass, data, expand_group=True), True
    if call.context.user_id:
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None:
            return None
        if not user.is_admin:
            allowed = [e for e in entities if user.permissions.check_entity(e, POLICY_CONTROL)]
            if whole and len(allowed) != len(entities):
                return None
            entities = allowed
    return sorted(e for e in entities if e.startswith("light."))


def setup_service_call_interceptor(hass, domain, service, intercept):
    """Preprocess a service call, retaining the original schema and handler."""
    try:
        existing = hass.services._services[domain][service]
        handler, schema = existing.job.target, existing.schema
    except (AttributeError, KeyError) as error:
        raise RuntimeError(f"Cannot intercept {domain}.{service}: HA private API unavailable") from error
    active = True

    async def proxy(call):
        after = None
        if active:
            try:
                data = dict(call.data)
                after = await intercept(call, data)
                call.data = ReadOnlyDict(data)
            except Exception:  # Keep the original light service usable.
                _LOGGER.exception("Could not intercept %s.%s", domain, service)
        try:
            if inspect.iscoroutinefunction(handler):
                return await handler(call)
            return handler(call)
        finally:
            if after:  # Runs once the original call has finished, or failed.
                after()

    hass.services.async_register(domain, service, proxy, schema)

    def remove():
        nonlocal active
        active = False
        # Another integration may have wrapped us since installation. In that case
        # leave an inert proxy in its chain rather than removing its handler.
        current = hass.services._services.get(domain, {}).get(service)
        if current and current.job.target is proxy:
            hass.services.async_register(domain, service, handler, schema)

    return remove
