"""The group: several controllers behind one device and one light.

The app's grouping is cloud-side, so a group here is a Home Assistant
construct — a config entry that owns no socket plus a light that forwards to the
member controllers. These tests pin the parts that make that safe:

* the group is a real device, so it has a device page and a stable entity id;
* a command reaches every member exactly once, even when the same controller is
  named twice (once directly, once through the group);
* the group's state follows the members rather than a copy of its own;
* the ``surplife.*`` services accept the group and expand it, which is the whole
  point of grouping;
* membership survives a controller being renamed, because it is stored as entry
  ids rather than entity ids.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_HOST,
    CONF_NAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.surplife.config_flow import _group_unique_id
from custom_components.surplife.const import (
    CONF_MAC,
    DOMAIN,
    SERVICE_APPLY_PRESET,
    SERVICE_SYNC_CLOCK,
)
from custom_components.surplife.groups import (
    GROUP_MEMBERS_KEY,
    default_group_name,
    is_group_entry,
)
from custom_components.surplife.protocol import Timer

from . import golden

EAST = "192.168.1.51"
WEST = "192.168.1.50"


def _state(power: bool = True):
    """A state frame in plain-colour mode."""
    from custom_components.surplife.protocol.state import parse_state

    frame = bytearray(golden.STATE_PRISTINE_INNER)
    frame[6] = 0x23 if power else 0x24
    frame[8] = 0x6E
    return parse_state(bytes(frame))


class _Clients:
    """The fake clients built so far, one per controller host.

    The coordinator constructs its own ``SurplifeClient``, so the fake has to
    hand back a distinct object per host and remember them for assertions. A
    dict keyed by host is also how the tests below say "every member was
    commanded" without depending on setup order.
    """

    def __init__(self) -> None:
        self.by_host: dict[str, Mock] = {}

    def __getitem__(self, host: str) -> Mock:
        return self.by_host[host]

    def __iter__(self):
        return iter(self.by_host.values())

    def __len__(self) -> int:
        return len(self.by_host)


@pytest.fixture
def clients():
    """Fake clients for every controller, indexed by host.

    Plain ``Mock``, never ``AsyncMock``: the coordinator hands every client call
    to ``asyncio.to_thread``, which would pass an ``AsyncMock``'s coroutine
    straight back.
    """
    holder = _Clients()

    def _client_for(host, *args, **kwargs):
        client = Mock()
        client.host = host
        client.get_state = Mock(side_effect=_state)
        client.set_power = Mock()
        client.set_levels = Mock()
        client.set_warm_white = Mock()
        client.set_effect = Mock()
        client.set_segments = Mock()
        client.set_scribble = Mock(return_value=1)
        client.get_timers = Mock(return_value=[Timer(slot=1, active=False)])
        client.sync_clock = Mock(return_value=dt.datetime(2026, 11, 5, 12, 0))
        client.set_timer = Mock()
        client.clear_timer = Mock()
        client.close = Mock()
        holder.by_host[host] = client
        return client

    with patch(
        "custom_components.surplife.coordinator.SurplifeClient", autospec=True
    ) as factory:
        factory.side_effect = _client_for
        yield holder


def _controller(hass: HomeAssistant, host: str, name: str, mac: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=name,
        data={
            CONF_HOST: host,
            CONF_NAME: name,
            CONF_MAC: mac,
            "hw_version": "AK001-ZJ21413",
            "firmware_info": "+ok=B6_26_20240514_ZG-BL-UFO",
            "led_count": 35,
        },
        unique_id=mac,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def two_controllers(hass: HomeAssistant, clients):
    """East and West, both loaded.

    Both entries are registered before anything is set up. Setting up the first
    one sets up the whole ``surplife`` component, which loads every entry that
    already exists — so a second ``async_setup`` would fail with
    ``OperationNotAllowed`` because that entry is already ``LOADED``.
    """
    entries = [
        _controller(hass, WEST, "Soffit West", "AABBCCDDEEFF"),
        _controller(hass, EAST, "Soffit East", "AABBCCDDEE11"),
    ]
    assert await hass.config_entries.async_setup(entries[0].entry_id)
    await hass.async_block_till_done()
    for entry in entries:
        assert entry.state is ConfigEntryState.LOADED
    return entries


@pytest.fixture
def group_entry(hass: HomeAssistant, two_controllers) -> MockConfigEntry:
    """One group over both controllers."""
    members = [e.entry_id for e in two_controllers]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Soffit",
        data={GROUP_MEMBERS_KEY: members},
        # The same identity the flow derives, so "already grouped" is testable.
        unique_id=_group_unique_id(members),
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def group(hass: HomeAssistant, group_entry) -> MockConfigEntry:
    """The group, loaded."""
    assert await hass.config_entries.async_setup(group_entry.entry_id)
    await hass.async_block_till_done()
    return group_entry


def _group_light(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    """The group's light entity id, from the registry rather than a guess."""
    registry = er.async_get(hass)
    return next(
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.domain == "light"
    )


# -- shape -----------------------------------------------------------------


async def test_group_appears_as_a_light_and_a_device(
    hass: HomeAssistant, group
) -> None:
    """A group is one light entity, attached to a device of its own."""
    from homeassistant.helpers import device_registry as dr

    entity_id = _group_light(hass, group)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_ON

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"group:{group.entry_id}"), group.entry_id
    )
    assert device is not None
    assert device.name == "Soffit"


async def test_group_does_not_get_controller_entities(
    hass: HomeAssistant, group
) -> None:
    """Only a light — no effect select, clock button or timer sensor.

    Those are properties of a strip. On a group they would have to arbitrate
    between members that may already disagree, so they stay on the controllers.
    """
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, group.entry_id)
    assert [entity.domain for entity in entities] == ["light"]


async def test_group_advertises_the_same_controls_as_a_controller(
    hass: HomeAssistant, group
) -> None:
    """Same colour channels and same effect list, or the card behaves differently."""
    from homeassistant.components.light import ColorMode

    from custom_components.surplife.const import STATIC_EFFECT
    from custom_components.surplife.protocol.constants import EFFECT_NAMES

    attributes = hass.states.get(_group_light(hass, group)).attributes
    assert ColorMode.RGBW in attributes["supported_color_modes"]
    assert ColorMode.WHITE in attributes["supported_color_modes"]
    assert tuple(attributes["effect_list"]) == (
        STATIC_EFFECT,
        *EFFECT_NAMES.values(),
    )


async def test_group_entry_is_recognised_by_its_members(hass: HomeAssistant, group) -> None:
    """An entry with no host and a member list is a group, and vice versa."""
    assert is_group_entry(group)
    assert not is_group_entry(
        next(e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != group.entry_id)
    )


# -- commands --------------------------------------------------------------


async def test_turn_off_reaches_every_member(
    hass: HomeAssistant, group, two_controllers, clients
) -> None:
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: _group_light(hass, group)}, blocking=True
    )
    await hass.async_block_till_done()

    for client in clients:
        client.set_power.assert_called_once_with(False)


async def test_turn_on_with_a_colour_reaches_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """A colour from the group card is one command per strip."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "rgb_color": (255, 0, 0)},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_levels.called
        kwargs = client.set_levels.call_args.kwargs
        assert kwargs["red"] == 255
        assert kwargs["green"] == 0
        assert kwargs["blue"] == 0


async def test_effect_reaches_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "effect": "Chase"},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_effect.called


# -- state -----------------------------------------------------------------


async def test_group_is_on_when_any_member_is_on(
    hass: HomeAssistant, group, two_controllers, clients
) -> None:
    """A strip left running means the roof is not dark."""
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: "light.soffit_east"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(_group_light(hass, group)).state == STATE_ON

    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: "light.soffit_west"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(_group_light(hass, group)).state == STATE_OFF


async def test_group_follows_a_member_turned_on_outside_the_group(
    hass: HomeAssistant, group
) -> None:
    """The group listens to its members, not only to its own commands."""
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: _group_light(hass, group)}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(_group_light(hass, group)).state == STATE_OFF

    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: "light.soffit_east"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(_group_light(hass, group)).state == STATE_ON


async def test_group_reports_brightness_as_the_mean_of_the_lit_strips(
    hass: HomeAssistant, group
) -> None:
    """One dim strip should pull the group down, not report the brightest."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: "light.soffit_east", "brightness": 100},
        blocking=True,
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: "light.soffit_west", "brightness": 200},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(_group_light(hass, group)).attributes["brightness"] == 150


async def test_group_attributes_name_the_members(
    hass: HomeAssistant, group
) -> None:
    """Services expand the group through this attribute, so it is a contract."""
    attributes = hass.states.get(_group_light(hass, group)).attributes
    assert len(attributes["surplife_group_members"]) == 2
    assert sorted(attributes["members"]) == ["Soffit East", "Soffit West"]
    assert sorted(attributes["entity_id"]) == ["light.soffit_east", "light.soffit_west"]


# -- services --------------------------------------------------------------


async def test_a_service_aimed_at_the_group_reaches_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """This is the point of the group: one call, every strip."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC_CLOCK,
        {ATTR_ENTITY_ID: _group_light(hass, group)},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.sync_clock.called


async def test_preset_broadcast_through_the_group(
    hass: HomeAssistant, group, clients
) -> None:
    await hass.services.async_call(
        DOMAIN,
        SERVICE_APPLY_PRESET,
        {ATTR_ENTITY_ID: _group_light(hass, group), "preset": "christmas"},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_effect.called


async def test_naming_a_group_and_one_of_its_members_sends_one_command(
    hass: HomeAssistant, group, clients
) -> None:
    """Overlapping targets must not double the command."""
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SYNC_CLOCK,
        {
            ATTR_ENTITY_ID: [
                _group_light(hass, group),
                "light.soffit_east",
            ]
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    calls = sum(client.sync_clock.call_count for client in clients)
    assert calls == 2


# -- membership ------------------------------------------------------------


async def test_group_survives_a_controller_rename(
    hass: HomeAssistant, group
) -> None:
    """Membership is entry ids, so renaming the entity must not break the group."""
    registry = er.async_get(hass)
    registry.async_update_entity("light.soffit_east", new_entity_id="light.east_run")

    await hass.async_block_till_done()

    assert hass.states.get("light.east_run") is not None
    assert hass.states.get(_group_light(hass, group)).state == STATE_ON
    assert "light.east_run" in hass.states.get(_group_light(hass, group)).attributes[
        "entity_id"
    ]


async def test_removing_a_controller_leaves_the_group_working(
    hass: HomeAssistant, group, two_controllers
) -> None:
    """A deleted member must not take the whole group down with it."""
    east = next(e for e in two_controllers if e.title == "Soffit East")
    await hass.config_entries.async_unload(east.entry_id)
    await hass.config_entries.async_remove(east.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(_group_light(hass, group))
    assert state is not None
    assert state.state != STATE_UNAVAILABLE


async def test_group_is_unavailable_when_no_member_is_available(
    hass: HomeAssistant, group
) -> None:
    """Every member unavailable means the group is too, rather than claiming off."""
    for entity_id in ("light.soffit_east", "light.soffit_west"):
        hass.states.async_set(entity_id, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert hass.states.get(_group_light(hass, group)).state == STATE_UNAVAILABLE


# -- naming ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["Soffit East", "Soffit West", "Soffit South"], "Soffit"),
        (["Roof", "Roof"], "Roof"),
        (["Front lights", "Back lights"], "Surplife group"),
        (["North"], "North"),
        (["Soffit East", "Soffit"], "Soffit"),
        (["", "  "], "Surplife group"),
    ],
)
def test_default_name_uses_the_words_in_common(names, expected) -> None:
    """A group named "Soffit" is what a user would have typed anyway."""
    assert default_group_name(names) == expected


# -- creating a group ------------------------------------------------------


async def test_add_integration_offers_a_menu_once_a_controller_exists(
    hass: HomeAssistant, two_controllers
) -> None:
    """Grouping has to be discoverable from Add Integration."""
    from homeassistant import config_entries as ce

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": ce.SOURCE_USER}
    )
    assert result["type"] == "menu"
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"controller", "group"}


async def test_group_flow_creates_one_entry_with_both_members(
    hass: HomeAssistant, two_controllers
) -> None:
    """The picker defaults to every controller, and the name comes from them."""
    from homeassistant import config_entries as ce

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": ce.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "group"}
    )
    assert result["type"] == "form"
    assert result["step_id"] == "group"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "entities": ["light.soffit_west", "light.soffit_east"],
            "name": "Soffit",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert result["title"] == "Soffit"
    entry = hass.config_entries.async_get_entry(result["result"].entry_id)
    assert entry is not None
    assert is_group_entry(entry)
    assert sorted(entry.data[GROUP_MEMBERS_KEY]) == sorted(
        e.entry_id for e in two_controllers
    )
    assert hass.states.get("light.soffit") is not None


async def test_group_flow_defaults_the_name_to_the_shared_word(
    hass: HomeAssistant, two_controllers
) -> None:
    """Leaving the name blank names the group after its members, not "Surplife"."""
    from homeassistant import config_entries as ce

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": ce.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "group"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "entities": ["light.soffit_west", "light.soffit_east"],
            "name": "",
        },
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert result["title"] == "Soffit"


async def test_group_needs_two_controllers(
    hass: HomeAssistant, two_controllers
) -> None:
    """One controller is not a group; the form explains rather than failing."""
    from homeassistant import config_entries as ce

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": ce.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "group"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"entities": ["light.soffit_west"], "name": "Soffit"},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "not_enough_controllers"}


async def test_the_same_controllers_cannot_be_grouped_twice(
    hass: HomeAssistant, group_entry, two_controllers
) -> None:
    """A duplicate group would shadow the first; membership is the identity."""
    from homeassistant import config_entries as ce

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": ce.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "group"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "entities": ["light.soffit_east", "light.soffit_west"],
            "name": "Roof",
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


# -- reconfiguring ---------------------------------------------------------


async def test_reconfigure_changes_membership(
    hass: HomeAssistant, group, two_controllers
) -> None:
    """Swapping a member reloads the entry and rebuilds the light."""
    from homeassistant import config_entries as ce

    third = _controller(hass, "192.168.1.52", "Soffit South", "AABBCCDDEE22")
    assert await hass.config_entries.async_setup(third.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": ce.SOURCE_RECONFIGURE, "entry_id": group.entry_id},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "entities": ["light.soffit_east", "light.soffit_south"],
            "name": "Soffit",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    updated = hass.config_entries.async_get_entry(group.entry_id)
    assert sorted(updated.data[GROUP_MEMBERS_KEY]) == sorted(
        [two_controllers[1].entry_id, third.entry_id]
    )
    assert "light.soffit_south" in hass.states.get(
        _group_light(hass, group)
    ).attributes["entity_id"]


async def test_controller_reconfigure_updates_the_host(
    hass: HomeAssistant, two_controllers
) -> None:
    """A DHCP move is fixed in place, keeping the entity ids."""
    from homeassistant import config_entries as ce

    west = two_controllers[0]
    with patch(
        "custom_components.surplife.config_flow.SurplifeClient", autospec=True
    ) as factory:
        factory.return_value.get_state = Mock(side_effect=_state)
        factory.return_value.close = Mock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": ce.SOURCE_RECONFIGURE, "entry_id": west.entry_id},
        )
        assert result["type"] == "form"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.60", CONF_NAME: "Soffit West"},
        )

    await hass.async_block_till_done()
    assert result["type"] == "abort"
    updated = hass.config_entries.async_get_entry(west.entry_id)
    assert updated.data[CONF_HOST] == "192.168.1.60"
    assert hass.states.get("light.soffit_west") is not None


async def test_controller_reconfigure_refuses_an_unreachable_host(
    hass: HomeAssistant, two_controllers
) -> None:
    """Do not save an address nothing answers on."""
    from homeassistant import config_entries as ce

    from custom_components.surplife.protocol import SurplifeConnectionError

    west = two_controllers[0]
    with patch(
        "custom_components.surplife.config_flow.SurplifeClient", autospec=True
    ) as factory:
        factory.return_value.get_state = Mock(
            side_effect=SurplifeConnectionError("no route")
        )
        factory.return_value.close = Mock()

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": ce.SOURCE_RECONFIGURE, "entry_id": west.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.61", CONF_NAME: "Soffit West"},
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "cannot_connect"}
    assert hass.config_entries.async_get_entry(west.entry_id).data[
        CONF_HOST
    ] == WEST
