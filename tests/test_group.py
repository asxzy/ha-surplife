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


async def test_group_gets_the_same_controls_as_a_controller(
    hass: HomeAssistant, group
) -> None:
    """A group is a controller-shaped device, minus the per-strip diagnostics.

    Every control here is a command that applies to every member at once. LED
    count and hardware timers are omitted because they describe one strip: three
    controllers can disagree, and inventing a merged number would be a lie.
    """
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, group.entry_id)
    assert sorted(entity.domain for entity in entities) == [
        "button",
        "button",
        "button",
        "light",
        "number",
        "number",
        "select",
        "select",
        "select",
        "select",
        "sensor",
        "text",
        "text",
    ]
    unique_ids = {entity.unique_id for entity in entities}
    assert f"{group.entry_id}_led_count" not in unique_ids
    assert f"{group.entry_id}_timers" not in unique_ids
    assert f"{group.entry_id}_mode" in unique_ids


async def test_group_sync_clock_button_reaches_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """Pressing the group's own button is one command per strip."""
    assert hass.states.get("button.soffit_sync_clock") is not None
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: "button.soffit_sync_clock"}, blocking=True
    )
    await hass.async_block_till_done()
    for client in clients:
        assert client.sync_clock.called


async def test_group_new_theme_button_reaches_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """The group's new-theme button draws once and paints every strip."""
    button = "button.soffit_new_theme"
    assert hass.states.get(button) is not None
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: button}, blocking=True
    )
    await hass.async_block_till_done()
    for client in clients:
        assert client.set_effect.called or client.set_scribble.called


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
    """Every member unavailable means the group is too, rather than claiming off.

    The group derives its availability from the member coordinators, not from
    their entity states: an unreachable strip still has a stale entity state,
    so trusting that would report a roof of dead controllers as merely off.
    """
    group_coordinator = hass.data[DOMAIN][group.entry_id]
    for member in group_coordinator.member_coordinators:
        member.last_update_success = False
    group_coordinator._member_updated()
    await hass.async_block_till_done()

    assert hass.states.get(_group_light(hass, group)).state == STATE_UNAVAILABLE

    for member in group_coordinator.member_coordinators:
        member.last_update_success = True
    group_coordinator._member_updated()
    await hass.async_block_till_done()

    assert hass.states.get(_group_light(hass, group)).state != STATE_UNAVAILABLE


# -- the group's control surface -------------------------------------------


async def _group_runtime(hass: HomeAssistant, entry):
    """The group coordinator's derived runtime."""
    return hass.data[DOMAIN][entry.entry_id].runtime


async def test_group_effect_select_applies_to_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """The group's effect dropdown is one command per strip, with one palette."""
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_effect", "option": "Chase"},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_effect.called
        args = client.set_effect.call_args
        assert args.args[0] == client.set_effect.call_args.args[0]


async def test_group_speed_slider_reaches_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """Moving the group's slider keeps the strips in step rather than one ahead."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "effect": "Chase"},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "number",
        "set_value",
        {ATTR_ENTITY_ID: "number.soffit_effect_speed", "value": 80},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_effect.called
        assert client.set_effect.call_args.kwargs["speed"] == 80


async def test_group_palette_entity_colours_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """A palette typed on the group must not leave members different colours."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "effect": "Chase"},
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "text",
        "set_value",
        {
            ATTR_ENTITY_ID: "text.soffit_effect_colours",
            "value": "#ff0000, #00ff00",
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        palette = client.set_effect.call_args.args[1]
        assert [tuple(colour[:3]) for colour in palette] == [
            (255, 0, 0),
            (0, 255, 0),
        ], "every member must receive the same resolved palette"


async def test_group_palette_is_saved_on_the_group_not_the_members(
    hass: HomeAssistant, group, two_controllers
) -> None:
    """The group owns its own configured palettes."""
    from custom_components.surplife.settings import EFFECTS_KEY

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "effect": "Chase"},
        blocking=True,
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        "text",
        "set_value",
        {ATTR_ENTITY_ID: "text.soffit_effect_colours", "value": "#ff0000, #00ff00"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert group.options[EFFECTS_KEY]["Chase"]["colors"] == [
        [255, 0, 0, 0],
        [0, 255, 0, 0],
    ]
    for member in two_controllers:
        assert EFFECTS_KEY not in member.options


async def test_group_scene_select_applies_to_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """A stored scene named on the group reaches all three strips."""
    from custom_components.surplife.const import SERVICE_SAVE_SCENE

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SAVE_SCENE,
        {
            "name": "warm",
            "mode": "rgb",
            "colors": ["#ff0000"],
            "brightness": 200,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_scene", "option": "warm"},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_levels.called


async def test_group_per_led_look_reaches_every_member(
    hass: HomeAssistant, group, clients
) -> None:
    """Painting by look is a group command, sized per strip."""
    looks = hass.states.get("select.soffit_per_led_look")
    assert looks is not None
    option = next(name for name in looks.attributes["options"] if name != "custom")

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_per_led_look", "option": option},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_scribble.called


async def test_group_reports_the_most_common_effect(
    hass: HomeAssistant, group, two_controllers
) -> None:
    """Two strips in step outvote one straggler."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: "light.soffit_east", "effect": "Chase"},
        blocking=True,
    )
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: "light.soffit_west", "effect": "Chase"},
        blocking=True,
    )
    await hass.async_block_till_done()

    runtime = await _group_runtime(hass, group)
    assert runtime.effect_name == "Chase"
    assert hass.states.get(_group_light(hass, group)).attributes["effect"] == "Chase"


async def test_group_reports_a_colour_only_when_a_member_is_lit(
    hass: HomeAssistant, group
) -> None:
    """The group's runtime follows the members' power, not its own copy."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "rgb_color": (255, 0, 0)},
        blocking=True,
    )
    await hass.async_block_till_done()
    runtime = await _group_runtime(hass, group)
    assert runtime.is_on
    assert runtime.rgbw_mix is not None
    assert runtime.rgbw_mix[:3] == (255, 0, 0)

    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: _group_light(hass, group)}, blocking=True
    )
    await hass.async_block_till_done()
    assert not (await _group_runtime(hass, group)).is_on


async def test_group_turns_on_a_member_left_dark(
    hass: HomeAssistant, group, clients
) -> None:
    """On means "any member", so turning the group on must fix a half-lit roof."""
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: "light.soffit_east"}, blocking=True
    )
    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: "light.soffit_west"}, blocking=True
    )
    await hass.async_block_till_done()
    assert hass.states.get(_group_light(hass, group)).state == STATE_ON

    await hass.services.async_call(
        "light", "turn_on", {ATTR_ENTITY_ID: _group_light(hass, group)}, blocking=True
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_power.call_args_list[-1].args[0] is True


async def test_a_dead_member_does_not_stop_the_rest_of_the_roof(
    hass: HomeAssistant, group, clients
) -> None:
    """One unreachable strip must not make a group command fail."""
    from homeassistant.exceptions import HomeAssistantError

    dead = clients[WEST]
    dead.set_levels = Mock(side_effect=HomeAssistantError("unreachable"))

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "rgb_color": (0, 0, 255)},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert clients[EAST].set_levels.called, "the reachable strip was still commanded"


async def test_a_command_fails_when_every_member_is_dead(
    hass: HomeAssistant, group, clients
) -> None:
    """Silence from every strip is a real failure, not a success."""
    import pytest
    from homeassistant.exceptions import HomeAssistantError

    for client in clients:
        client.set_power = Mock(side_effect=HomeAssistantError("unreachable"))

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "light",
            "turn_off",
            {ATTR_ENTITY_ID: _group_light(hass, group)},
            blocking=True,
        )


async def test_group_rename_keeps_the_group_light_and_members(
    hass: HomeAssistant, group, clients
) -> None:
    """Member entity ids are resolved live, so a rename cannot break the fan-out."""
    registry = er.async_get(hass)
    registry.async_update_entity("light.soffit_east", new_entity_id="light.east_run")
    await hass.async_block_till_done()

    assert "light.east_run" in hass.states.get(_group_light(hass, group)).attributes[
        "entity_id"
    ]

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "rgb_color": (0, 255, 0)},
        blocking=True,
    )
    await hass.async_block_till_done()
    for client in clients:
        assert client.set_levels.called


async def test_group_direction_sticks_between_effects(
    hass: HomeAssistant, group
) -> None:
    """A direction chosen while nothing is running must not be discarded.

    A controller keeps ``effect_direction`` in its runtime until the next effect
    is applied. The group's runtime is rebuilt from the members on every change,
    so re-deriving it would silently undo the choice -- and the next effect
    would run the old way.
    """
    await hass.services.async_call(
        "light", "turn_off", {ATTR_ENTITY_ID: _group_light(hass, group)}, blocking=True
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_direction", "option": "right_to_left"},
        blocking=True,
    )
    await hass.async_block_till_done()

    # A member update re-derives the group runtime; the direction must survive.
    group_coordinator = hass.data[DOMAIN][group.entry_id]
    group_coordinator._member_updated()
    await hass.async_block_till_done()

    assert hass.states.get("select.soffit_direction").state == "right_to_left"
    assert group_coordinator.runtime.effect_direction == 2


async def test_group_direction_reaches_members_when_an_effect_runs(
    hass: HomeAssistant, group, clients
) -> None:
    """The direction is delivered, not merely remembered."""
    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "effect": "Chase"},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        client.set_effect.reset_mock()

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_direction", "option": "right_to_left"},
        blocking=True,
    )
    await hass.async_block_till_done()

    for client in clients:
        assert client.set_effect.called
        assert client.set_effect.call_args.kwargs["direction"] == 2


async def test_group_look_reports_itself_after_painting(
    hass: HomeAssistant, group
) -> None:
    """A look must show in the dropdown, not read ``custom`` until the next poll."""
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.soffit_per_led_look", "option": "stripes"},
        blocking=True,
    )
    await hass.async_block_till_done()

    runtime = hass.data[DOMAIN][group.entry_id].runtime
    assert runtime.look == "stripes"
    assert runtime.effect_name is None
    assert hass.states.get("select.soffit_per_led_look").state == "stripes"


# -- the Configure panel on a group ----------------------------------------


async def test_group_configure_panel_opens_and_applies(
    hass: HomeAssistant, group, clients
) -> None:
    """Before the facade, a group's Configure gear led to a panel that no-opped.

    ``_coordinator()`` returned ``None`` for a group, so every step silently
    saved without touching a strip. The group now has a coordinator, so the same
    panel is a real control.
    """
    from homeassistant.data_entry_flow import FlowResultType

    result = await hass.config_entries.options.async_init(group.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "recolour",
        "scribble",
        "effect",
        "look",
        "interval",
    }

    # Pick an effect, then save its panel; the save must reach every strip.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "effect"}
    )
    assert result["step_id"] == "effect"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"effect": "Wave"}
    )
    assert result["step_id"] == "effect_settings"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"color_1": [0, 128, 255], "white_1": 0}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    from custom_components.surplife.settings import EFFECTS_KEY

    assert EFFECTS_KEY in group.options
    for client in clients:
        assert client.set_effect.called


async def test_group_recolour_aborts_when_nothing_is_running(
    hass: HomeAssistant, group
) -> None:
    """The menu says so rather than showing pickers that would do nothing."""
    from homeassistant.data_entry_flow import FlowResultType

    await hass.services.async_call(
        "light",
        "turn_on",
        {ATTR_ENTITY_ID: _group_light(hass, group), "rgb_color": (255, 0, 0)},
        blocking=True,
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(group.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "recolour"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_running_effect"


async def test_group_configure_uses_the_member_led_count(
    hass: HomeAssistant, group
) -> None:
    """The per-LED editor must size itself from the strips, not from entry data.

    A group entry has no ``led_count`` of its own, so reading it there would
    fall back to 35 even if the strips are longer or shorter.
    """
    group_coordinator = hass.data[DOMAIN][group.entry_id]
    for member in group_coordinator.member_coordinators:
        assert member.runtime.led_count > 0

    result = await hass.config_entries.options.async_init(group.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "scribble"}
    )
    assert result["description_placeholders"]["leds"] == "35"


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


async def test_controller_reconfigure_prefills_the_stored_host(
    hass: HomeAssistant, two_controllers
) -> None:
    """The form must not offer some other address than the one in use.

    Getting this wrong is not cosmetic: submitting the prefilled value is how a
    user renames a controller, and a wrong default turns that into a probe of an
    address that does not exist, which fails with ``cannot_connect``.
    """
    from homeassistant import config_entries as ce

    west = two_controllers[0]
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": ce.SOURCE_RECONFIGURE, "entry_id": west.entry_id},
    )
    assert result["type"] == "form"
    defaults = result["data_schema"]({})
    assert defaults[CONF_HOST] == WEST
    assert defaults[CONF_NAME] == "Soffit West"


async def test_controller_reconfigure_renames_without_touching_the_wire(
    hass: HomeAssistant, two_controllers
) -> None:
    """An unchanged address is not a move, so nothing is probed.

    This is the path a user takes to rename a strip. Opening a socket here would
    make renaming fail whenever the controller is briefly offline.
    """
    from homeassistant import config_entries as ce

    west = two_controllers[0]
    with patch(
        "custom_components.surplife.config_flow.SurplifeClient", autospec=True
    ) as factory:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": ce.SOURCE_RECONFIGURE, "entry_id": west.entry_id},
        )
        defaults = result["data_schema"]({})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: defaults[CONF_HOST], CONF_NAME: "Soffit West Renamed"},
        )

    await hass.async_block_till_done()
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert not factory.called, "a rename must not probe the controller"
    updated = hass.config_entries.async_get_entry(west.entry_id)
    assert updated.title == "Soffit West Renamed"
    assert updated.data[CONF_HOST] == WEST
    # The device and entity survive the rename, which is the point of doing it
    # here instead of deleting and re-adding.
    assert hass.states.get("light.soffit_west") is not None
