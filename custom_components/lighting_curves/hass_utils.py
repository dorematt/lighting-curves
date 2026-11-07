"""Service hook adapted from Adaptive Lighting (https://github.com/basnijholt/adaptive-lighting).

Apache-2.0, copyright its authors/contributors. Modified for Lighting Curves:
setup_service_call_interceptor and target_entities only; safe chained removal.
"""
from __future__ import annotations

import inspect
import logging

from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.util.read_only_dict import ReadOnlyDict

_LOGGER = logging.getLogger(__name__)


def target_entities(hass, data):
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(data), expand_group=False)
    return selected.referenced | selected.indirectly_referenced


def setup_service_call_interceptor(hass, domain, service, intercept):
    """Preprocess a service call, retaining the original schema and handler."""
    try:
        existing = hass.services._services[domain][service]
        handler, schema = existing.job.target, existing.schema
    except (AttributeError, KeyError) as error:
        raise RuntimeError(f"Cannot intercept {domain}.{service}: HA private API unavailable") from error
    active = True

    async def proxy(call):
        if active:
            try:
                data = dict(call.data)
                await intercept(call, data)
                call.data = ReadOnlyDict(data)
            except Exception:  # Keep the original light service usable.
                _LOGGER.exception("Could not intercept %s.%s", domain, service)
        if inspect.iscoroutinefunction(handler):
            return await handler(call)
        return handler(call)

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
