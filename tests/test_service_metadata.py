"""Cross-checks between the service handlers and the shipped UI metadata.

Home Assistant renders ``services.yaml`` in the Developer Tools UI and uses
``strings.json`` for the labels in it. Neither is executed, so a service can be
registered with no UI, or documented with no handler, and nothing notices until
a user tries to call it. These tests close that gap.

``strings.json`` is also validated separately by Home Assistant at runtime, and
rejects non-text values under ``services``, so the shape is asserted here too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from homeassistant.core import HomeAssistant

from custom_components.surplife.const import DOMAIN

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "surplife"


def _documented() -> dict:
    with (COMPONENT / "services.yaml").open() as handle:
        return yaml.safe_load(handle)


def _strings() -> dict:
    with (COMPONENT / "strings.json").open() as handle:
        return json.load(handle)


def _registered(hass: HomeAssistant) -> set[str]:
    return set(hass.services.async_services_for_domain(DOMAIN))


async def test_every_documented_service_has_a_handler(
    hass: HomeAssistant, loaded_entry
) -> None:
    documented = set(_documented())
    registered = _registered(hass)
    missing = documented - registered
    assert not missing, f"documented but not registered: {sorted(missing)}"


async def test_every_registered_service_is_documented(
    hass: HomeAssistant, loaded_entry
) -> None:
    documented = set(_documented())
    undocumented = _registered(hass) - documented
    assert not undocumented, (
        f"registered but not in services.yaml: {sorted(undocumented)}"
    )


async def test_documented_fields_match_the_schema(
    hass: HomeAssistant, loaded_entry
) -> None:
    """Every ``services.yaml`` field must be accepted by the handler's schema.

    A field that the UI offers but the schema rejects is the worst kind of
    mismatch: it looks available and then fails.
    """
    documented = _documented()
    for name in sorted(documented):
        service = hass.services.async_services_for_domain(DOMAIN).get(name)
        if service is None or service.schema is None:
            continue
        accepted = _schema_keys(service.schema)
        for field in documented[name].get("fields", {}):
            assert field in accepted, (
                f"{name}.{field} is documented but the schema accepts {sorted(accepted)}"
            )


async def test_required_fields_are_marked_required(
    hass: HomeAssistant, loaded_entry
) -> None:
    """A field the schema requires should not be optional in the UI."""
    import voluptuous as vol

    documented = _documented()
    for name in sorted(documented):
        service = hass.services.async_services_for_domain(DOMAIN).get(name)
        if service is None or service.schema is None:
            continue
        required = {
            str(key.schema)
            for key in service.schema.schema
            if isinstance(key, vol.Required)
        }
        for field, spec in documented[name].get("fields", {}).items():
            if field in required:
                assert spec.get("required") is True, (
                    f"{name}.{field} is required by the schema but optional in the UI"
                )


def _schema_keys(schema) -> set[str]:
    """The field names a voluptuous schema accepts."""
    return {str(getattr(key, "schema", key)) for key in schema.schema}


def test_services_yaml_passes_home_assistant_validation() -> None:
    """``services.yaml`` must survive Home Assistant's own loader.

    The loader runs the whole file through one schema and, on any error,
    logs a warning and returns ``{}`` — so a single bad selector silently
    removes **every** field from **every** service in the integration. The
    failure is invisible from the outside: the services still exist and still
    work when called from YAML, they just render no inputs at all, which reads
    as "there is nowhere to pick a colour".

    Checking field names and handler signatures does not catch it, because the
    file is structurally fine; only the selector options are wrong.
    """
    from homeassistant.helpers.service import _SERVICES_SCHEMA

    document = _documented()
    validated = _SERVICES_SCHEMA(document)
    assert set(validated) == set(document)


def test_multi_colour_selectors_offer_a_colour_picker() -> None:
    """The colour fields must render as pickers, not blank rows.

    ``color_rgb`` takes a single ``[r, g, b]`` and has no ``multiple`` option,
    so a list of colours needs the ``object`` selector wrapping it. This pins
    that shape, and checks the field really contains a colour picker.
    """
    document = _documented()
    for service, field in (
        ("set_segments", "colors"),
        ("apply_look", "palette"),
        ("save_scene", "colors"),
        ("save_scene", "segments"),
    ):
        selector = document[service]["fields"][field]["selector"]
        assert "object" in selector, f"{service}.{field} must use the object selector"
        config = selector["object"]
        assert config.get("multiple") is True
        nested = config["fields"]["color"]["selector"]
        assert "color_rgb" in nested, f"{service}.{field} has no colour picker"

    for service, field in (
        ("set_segments", "colors"),
        ("save_scene", "segments"),
    ):
        white = document[service]["fields"][field]["selector"]["object"]["fields"][
            "white"
        ]
        assert white["selector"]["number"]["max"] == 100


def test_strings_cover_every_service() -> None:
    documented = set(_documented())
    described = set(_strings()["services"])
    missing = documented - described
    assert not missing, f"no strings.json entry for: {sorted(missing)}"


def test_strings_have_no_orphan_services() -> None:
    documented = set(_documented())
    described = set(_strings()["services"])
    orphans = described - documented
    assert not orphans, f"strings.json describes unknown services: {sorted(orphans)}"


def test_service_strings_are_text_only() -> None:
    """Home Assistant rejects non-text values under ``services``.

    This is a real runtime error at setup, so it is worth catching here rather
    than in a restart log.
    """
    for name, spec in _strings()["services"].items():
        assert isinstance(spec.get("name"), str), f"{name} has no name"
        assert isinstance(spec.get("description"), str), f"{name} has no description"
        for field, field_spec in spec.get("fields", {}).items():
            for key, value in field_spec.items():
                assert isinstance(value, str), (
                    f"{name}.{field}.{key} is {type(value).__name__}, must be text"
                )


def test_strings_cover_every_documented_field() -> None:
    """Every field in ``services.yaml`` needs a label in ``strings.json``.

    Home Assistant falls back to the raw field name when a label is missing, so
    a user sees ``scribble_effect`` instead of "Per-LED effect". That is a
    usability bug, not a crash, which is exactly the kind that goes unnoticed.
    """
    for service, spec in _documented().items():
        described = _strings()["services"][service].get("fields", {})
        for field in spec.get("fields", {}):
            assert field in described, f"{service}.{field} has no strings.json entry"


def test_translations_match_strings() -> None:
    """The compiled translation must not drift from the source strings."""
    with (COMPONENT / "translations" / "en.json").open() as handle:
        translated = json.load(handle)
    assert translated == _strings()


@pytest.mark.parametrize("platform", ["select", "text", "sensor", "button"])
def test_entity_strings_have_names(platform: str) -> None:
    for key, spec in _strings()["entity"].get(platform, {}).items():
        assert spec.get("name"), f"entity.{platform}.{key} has no name"


def test_every_entity_platform_is_registered() -> None:
    """A platform module that is never forwarded is dead code."""
    from custom_components.surplife import PLATFORMS

    forwarded = {platform.value for platform in PLATFORMS}
    modules = {
        path.stem
        for path in COMPONENT.glob("*.py")
        if path.stem
        in {
            "binary_sensor",
            "button",
            "camera",
            "climate",
            "cover",
            "fan",
            "light",
            "number",
            "select",
            "sensor",
            "siren",
            "switch",
            "text",
        }
    }
    missing = modules - forwarded
    assert not missing, f"platform modules not forwarded: {sorted(missing)}"
