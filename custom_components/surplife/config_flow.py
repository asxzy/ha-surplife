"""Config flow: discover controllers over UDP, or accept a manual IP.

The options half of this module is the integration's control panel. See
:class:`SurplifeOptionsFlow` for why the panel is generated per effect rather
than showing every parameter at once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    ColorRGBSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_FIRMWARE_INFO,
    CONF_HW_VERSION,
    CONF_LED_COUNT,
    CONF_MAC,
    CONF_MODEL_INFO,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .effect_profile import PaletteMode, profile_for
from .protocol import SurplifeClient, SurplifeError, async_discover, is_supported
from .protocol.constants import EFFECT_NAMES, SCRIBBLE_EFFECT_NAMES
from .scribble_looks import LOOK_LIST, LOOKS
from .settings import (
    EffectSettings,
    LookSettings,
    effect_settings,
    look_settings,
    with_effect,
    with_look,
)

_LOGGER = logging.getLogger(__name__)

MANUAL_OPTION = "__manual__"

#: Options-flow step ids. Each one is also a ``strings.json`` key under
#: ``options.step``, so renaming one without the strings blanks its label.
STEP_INTERVAL = "interval"
STEP_EFFECT = "effect"
STEP_EFFECT_SETTINGS = "effect_settings"
STEP_RECOLOUR = "recolour"
STEP_LOOK = "look"
STEP_LOOK_SETTINGS = "look_settings"


def _default_name(host: str) -> str:
    """A usable fallback name when the user does not supply one.

    ``DEFAULT_NAME`` alone would make every controller in the house the same
    name, so the last octet is appended -- enough to tell three soffits apart
    without making the user type anything.
    """
    return f"{DEFAULT_NAME} {host.rsplit('.', 1)[-1]}"


MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


class SurplifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovered: dict[str, Any] = {}
        self._pending: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer discovered controllers, or fall through to manual entry."""
        if user_input is not None:
            host = user_input[CONF_HOST]
            if host == MANUAL_OPTION:
                return await self.async_step_manual()
            device = self._discovered.get(host)
            if device is None:
                return await self.async_step_manual()
            return await self._async_validate(
                host=device.host,
                name=user_input.get(CONF_NAME) or _default_name(device.host),
                mac=device.mac,
                hw_version=device.hw_version,
                model_info=device.model_info,
                firmware_info=device.firmware_info,
            )

        try:
            devices = await async_discover(timeout=3.0)
        except OSError as err:
            _LOGGER.debug("discovery failed: %s", err)
            devices = []

        supported = [device for device in devices if is_supported(device)]
        if not supported:
            return await self.async_step_manual()

        self._discovered = {device.host: device for device in supported}
        options = [
            SelectOptionDict(
                value=device.host,
                label=f"{device.host} ({device.mac})",
            )
            for device in supported
        ]
        options.append(
            SelectOptionDict(value=MANUAL_OPTION, label="Enter an IP address")
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=supported[0].host): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    ),
                    # Asking for the name here rather than in a second step
                    # keeps adding a controller to one screen. Leaving it blank
                    # still gives a usable name, so it never blocks.
                    vol.Optional(CONF_NAME, default=""): str,
                }
            ),
            description_placeholders={"count": str(len(supported))},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for an IP address and optional name."""
        if user_input is not None:
            return await self._async_validate(
                host=user_input[CONF_HOST].strip(),
                name=user_input.get(CONF_NAME) or DEFAULT_NAME,
            )
        return self.async_show_form(step_id="manual", data_schema=MANUAL_SCHEMA)

    async def _async_validate(
        self,
        *,
        host: str,
        name: str,
        mac: str | None = None,
        hw_version: str | None = None,
        model_info: str | None = None,
        firmware_info: str | None = None,
    ) -> ConfigFlowResult:
        """Probe the controller and create the entry."""
        errors: dict[str, str] = {}
        state = None
        try:
            client = SurplifeClient(host)
            state = await asyncio.to_thread(client.get_state)
            await asyncio.to_thread(client.close)
        except (SurplifeError, OSError) as err:
            _LOGGER.debug("probe of %s failed: %s", host, err)
            errors["base"] = "cannot_connect"

        if state is not None and state.model != 0xB6:
            errors["base"] = "not_supported"

        if errors:
            schema = MANUAL_SCHEMA
            return self.async_show_form(
                step_id="manual", data_schema=schema, errors=errors
            )

        assert state is not None
        unique = mac or host
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        data = {
            CONF_HOST: host,
            CONF_NAME: name,
            CONF_MAC: mac or host,
            CONF_HW_VERSION: hw_version,
            CONF_MODEL_INFO: model_info,
            CONF_FIRMWARE_INFO: firmware_info,
            CONF_LED_COUNT: state.led_count,
        }
        return self.async_create_entry(title=name, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return SurplifeOptionsFlow()


# -- options panel ----------------------------------------------------------


def _slider(*, minimum: int = 0, maximum: int = 100, step: int = 1) -> NumberSelector:
    """A 0-100 slider, the shape every effect parameter uses."""
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum, max=maximum, step=step, mode=NumberSelectorMode.SLIDER
        )
    )


def _direction_selector() -> SelectSelector:
    """Left-to-right or right-to-left, as the wire byte's two values."""
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value="l2r", label="Left to right"),
                SelectOptionDict(value="r2l", label="Right to left"),
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


def _bounded(value: Any, low: int, high: int) -> int:
    """Coerce and clamp a selector value into a wire range."""
    return max(low, min(high, int(value)))


def _colour_of(rgb: Any, white: Any) -> tuple[int, int, int, int] | None:
    """Combine one colour picker and its W level into an RGBW tuple.

    Returns ``None`` when the picker was left empty, which means "I have no
    opinion" rather than "black".

    The W level is stored as **0-100**, the protocol layer's unit for the white
    channel. Home Assistant's own ``rgbw_color`` uses 0-255 and converts at that
    boundary; nothing here ever sees a 0-255 white.
    """
    if rgb is None:
        return None
    red, green, blue = (int(channel) for channel in rgb)
    return (red, green, blue, _bounded(white or 0, 0, 100))


class SurplifeOptionsFlow(OptionsFlow):
    """Configure the poll interval, then each effect and look on its own.

    **Why a menu and not one form.** Home Assistant fixes a flow's schema when
    the form is shown; it cannot add a field when a dropdown changes without a
    round trip. There are 24 effects and 9 looks, and each wants different
    controls, so the flow asks *which* one to configure and then builds the form
    for that one. The result is a panel with only the fields that do something,
    instead of a wall of controls where most are inert.

    **Saving applies.** Each panel writes through to the controller before the
    new options are saved, so adjusting a slider and pressing submit changes
    the strip immediately. The saved values are then what selecting that effect
    later restores.
    """

    def __init__(self) -> None:
        """Initialise the flow."""
        self._pending_effect: str | None = None
        self._pending_look: str | None = None
        #: How many colour rows the effect panel is showing. Rows are added and
        #: removed by round trip, so the count has to survive between renders.
        self._panel_rows: int = 0
        #: Colours read back from the form during an add/remove round trip, so a
        #: colour already picked is not lost when the form is re-rendered.
        #: Positional, and ``None`` for an empty row.
        self._draft_rows: list[list[int] | None] | None = None

    @property
    def _options(self) -> dict[str, Any]:
        """The entry's current options, always a plain dict."""
        return dict(self.config_entry.options)

    # -- menu --------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer what to configure."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[STEP_RECOLOUR, STEP_EFFECT, STEP_LOOK, STEP_INTERVAL],
            description_placeholders=self._overview(),
        )

    def _overview(self) -> dict[str, str]:
        """Counts, so the menu says how much has been configured."""
        effects = self._options.get("effects") or {}
        looks = self._options.get("looks") or {}
        return {
            "effects_configured": str(len(effects)),
            "effects_total": str(len(EFFECT_NAMES)),
            "looks_configured": str(len(looks)),
            "looks_total": str(len(LOOKS)),
        }

    # -- poll interval -----------------------------------------------------

    async def async_step_interval(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """How often to poll the state frame."""
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self._options, **user_input}
            )
        return self.async_show_form(
            step_id=STEP_INTERVAL,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "scan_interval",
                        default=self._options.get(
                            "scan_interval", DEFAULT_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10, max=600, step=5, mode=NumberSelectorMode.BOX
                        )
                    )
                }
            ),
        )

    # -- effects -----------------------------------------------------------

    async def async_step_effect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which effect to configure.

        Each option is labelled with what that effect actually uses, so the
        choice can be made without applying anything to find out.
        """
        if user_input is not None:
            self._pending_effect = user_input["effect"]
            return await self.async_step_effect_settings()

        options = []
        for name in EFFECT_NAMES.values():
            settings = profile_for(name)
            hint = {
                PaletteMode.SINGLE: "1 colour",
                PaletteMode.GENERATED: "no colour",
                PaletteMode.COLOURS: (
                    f"1-{settings.max_colours} colours"
                    if settings.max_colours > 1
                    else "1 colour"
                ),
            }[settings.palette]
            configured = (
                " · saved" if effect_settings(self._options, name) is not None else ""
            )
            options.append(
                SelectOptionDict(value=name, label=f"{name} — {hint}{configured}")
            )

        return self.async_show_form(
            step_id=STEP_EFFECT,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "effect", default=next(iter(EFFECT_NAMES.values()))
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_recolour(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the colours of the effect that is running, with colour wheels.

        **Why this exists next to the effect panel.** ``Configure an effect``
        asks which effect to configure and then builds that effect's whole
        panel -- speed, density, direction and all. That is right for setting an
        effect up. It is the wrong shape for the question "this is running, I
        just want it a different colour", which is two menus deep from the
        device page and offers fields that are already correct.

        So this step skips the effect picker entirely: it targets whatever is
        showing right now, shows only its colour rows, and applies them the way
        the light card's wheel does -- the effect keeps running.

        There is no effect running to recolour in a flat-colour state, so the
        step says so rather than showing empty pickers that would do nothing.
        """
        coordinator = self._coordinator()
        runtime = getattr(coordinator, "runtime", None)
        name = getattr(runtime, "effect_name", None)

        if name is None:
            return self.async_abort(reason="no_running_effect")

        profile = profile_for(name)
        if profile.palette is PaletteMode.GENERATED:
            return self.async_abort(reason="effect_paints_its_own_colours")

        stored = effect_settings(self._options, name) or EffectSettings()
        # What the strip is showing right now beats what was last saved: this
        # panel is about the running effect, and a recolour from the card or the
        # palette entity may have changed it since.
        current = list(getattr(runtime, "palette", ()) or stored.colors)

        if user_input is not None:
            # Add/remove are round trips, not saves, exactly as in the effect
            # panel: the row count changes and the form comes back with the
            # colours already picked still in place.
            if user_input.pop("add_colour", False) and (
                self._panel_rows < profile.max_colours
            ):
                self._draft_rows = self._rows_from_form(user_input)
                self._panel_rows += 1
                self._draft_rows.append(None)
                return await self.async_step_recolour()
            if user_input.pop("remove_colour", False) and self._panel_rows > 1:
                self._draft_rows = self._rows_from_form(user_input)
                self._panel_rows -= 1
                del self._draft_rows[self._panel_rows :]
                return await self.async_step_recolour()

            colours = self._colours_from_rows(profile, user_input)
            # Row one always has a colour: it is pre-filled from what the strip
            # is showing, and a colour picker cannot submit an empty value. So
            # ``colours`` is never empty here, and there is no "no colours"
            # error to report.
            if not await self._recolour(name, colours):
                return self.async_abort(reason="effect_stopped")
            self._panel_rows = 0
            self._draft_rows = None
            return self.async_create_entry(title="", data=self._options)

        if not self._panel_rows:
            self._panel_rows = max(1, min(len(current) or profile.panel_colours,
                                          profile.max_colours))
        return self.async_show_form(
            step_id=STEP_RECOLOUR,
            data_schema=self._recolour_schema(profile, current),
            description_placeholders={
                "effect": name,
                "max": str(profile.max_colours),
                "rows": str(self._panel_rows),
            },
        )

    def _recolour_schema(self, profile: Any, current: list[Any]) -> vol.Schema:
        """Colour rows for the running effect, pre-filled from what it shows."""
        draft = (
            self._draft_rows
            if self._draft_rows is not None
            else [list(colour) for colour in current]
        )
        fields: dict[Any, Any] = {}
        for slot in range(self._panel_rows):
            default = draft[slot] if slot < len(draft) else None
            fields[
                vol.Optional(
                    f"color_{slot + 1}",
                    **({"default": list(default[:3])} if default else {}),
                )
            ] = ColorRGBSelector()
            fields[
                vol.Optional(
                    f"white_{slot + 1}",
                    default=(default[3] if default and len(default) > 3 else 0),
                )
            ] = _slider()

        if 0 < self._panel_rows < profile.max_colours:
            fields[vol.Optional("add_colour", default=False)] = BooleanSelector()
        if self._panel_rows > 1:
            fields[vol.Optional("remove_colour", default=False)] = BooleanSelector()
        return vol.Schema(fields)

    def _colours_from_rows(
        self, profile: Any, user_input: dict[str, Any]
    ) -> list[tuple[int, int, int, int]]:
        """Read the submitted colour rows, skipping ones left empty.

        An empty row is "no opinion", not black, so a two-stop gradient with the
        second stop cleared is a one-stop gradient rather than a fade to black.
        """
        colours: list[tuple[int, int, int, int]] = []
        for slot in range(min(self._panel_rows, profile.max_colours)):
            colour = _colour_of(
                user_input.get(f"color_{slot + 1}"),
                user_input.get(f"white_{slot + 1}"),
            )
            if colour is not None:
                colours.append(colour)
        return colours

    async def _recolour(
        self, name: str, colours: list[tuple[int, int, int, int]]
    ) -> bool:
        """Apply and persist the new palette without stopping the effect.

        ``async_recolour_active_effect`` already writes the palette back to the
        entry's options and claims them, so there is nothing to save here and
        the update listener must not re-send the effect. Returns ``False`` when
        the effect stopped between showing the form and submitting it.
        """
        coordinator = self._coordinator()
        if coordinator is None:
            return False
        try:
            return await coordinator.async_recolour_active_effect(colours)
        except HomeAssistantError as err:
            _LOGGER.debug("could not recolour %s: %s", name, err)
            return False

    async def async_step_effect_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The generated panel for one effect, one colour row at a time.

        **Why the palette is added one colour at a time.** Home Assistant fixes
        a form's schema when the form is shown, so a field cannot appear in
        response to a click without a round trip. Effects take between one and
        eight colours, and showing eight pickers on ``Wave`` (which uses one)
        wastes the user's attention on seven inert controls.

        So the form shows exactly the colours the effect currently has, plus an
        **Add another colour** tick that returns to this step with one more row
        -- but only while the effect's own cap has not been reached. The result
        behaves like the phone app: colours are built up one at a time, and the
        form never offers a slot the effect ignores.
        """
        name = self._pending_effect
        if name is None:  # defensive: the picker always sets this
            return await self.async_step_effect()
        profile = profile_for(name)
        stored = effect_settings(self._options, name) or EffectSettings()

        if user_input is not None:
            # A tick on either toggle is a round trip, not a save: it changes
            # how many rows the form has and shows it again. The colours already
            # on the form are read back **by position** so adding or removing a
            # row never shuffles the ones above it, and nothing is retyped.
            if user_input.pop("add_colour", False) and (
                self._panel_rows < profile.max_colours
            ):
                self._draft_rows = self._rows_from_form(user_input)
                self._panel_rows += 1
                self._draft_rows.append(None)
                return await self.async_step_effect_settings()
            if user_input.pop("remove_colour", False) and self._panel_rows > 1:
                self._draft_rows = self._rows_from_form(user_input)
                self._panel_rows -= 1
                del self._draft_rows[self._panel_rows :]
                return await self.async_step_effect_settings()

            settings = self._effect_from_form(profile, stored, user_input)
            options = with_effect(self._options, name, settings)
            await self._apply_effect(name, settings, options)
            self._pending_effect = None
            self._panel_rows = 0
            self._draft_rows = None
            return self.async_create_entry(title="", data=options)

        # First visit to this effect: start from however many colours it is
        # stored with, falling back to the palette the effect is designed
        # around. Never fewer than one for a palette effect, never more than
        # the cap. An effect that paints its own colours gets no rows at all.
        if profile.palette is PaletteMode.GENERATED:
            rows = 0
        elif self._panel_rows:
            rows = self._panel_rows
        else:
            rows = max(1, min(len(stored.colors) or profile.panel_colours,
                              profile.max_colours))
        self._panel_rows = rows
        draft = (
            self._draft_rows
            if self._draft_rows is not None
            else [list(colour) for colour in stored.colors]
        )

        fields: dict[Any, Any] = {}
        for slot in range(rows):
            default = draft[slot] if slot < len(draft) else None
            fields[
                vol.Optional(
                    f"color_{slot + 1}",
                    **({"default": list(default[:3])} if default else {}),
                )
            ] = ColorRGBSelector()
            fields[
                vol.Optional(
                    f"white_{slot + 1}",
                    default=default[3] if default else 0,
                )
            ] = _slider()

        if profile.speed:
            fields[vol.Optional("speed", default=stored.speed or 50)] = _slider()
        if profile.density:
            fields[vol.Optional("density", default=stored.density or 50)] = _slider()
        if profile.direction:
            fields[
                vol.Optional(
                    "direction",
                    default="l2r" if stored.direction in (None, 1) else "r2l",
                )
            ] = _direction_selector()

        if 0 < rows < profile.max_colours:
            fields[vol.Optional("add_colour", default=False)] = BooleanSelector()
        if rows > 1:
            fields[vol.Optional("remove_colour", default=False)] = BooleanSelector()

        return self.async_show_form(
            step_id=STEP_EFFECT_SETTINGS,
            data_schema=vol.Schema(fields),
            description_placeholders={
                "effect": name,
                "description": profile.description,
                "rows": str(rows),
                "max": str(profile.max_colours),
            },
        )

    def _rows_from_form(self, user_input: dict[str, Any]) -> list[list[int] | None]:
        """Read the colour rows currently on the form, position by position.

        Used by the add/remove round trips, where the row count is about to
        change: positions must be preserved so adding a row does not shuffle
        the colours the user already picked. An empty row stays ``None`` in its
        place rather than being dropped, which would silently renumber the ones
        after it.
        """
        rows: list[list[int] | None] = []
        for slot in range(self._panel_rows):
            colour = _colour_of(
                user_input.get(f"color_{slot + 1}"),
                user_input.get(f"white_{slot + 1}"),
            )
            rows.append(list(colour) if colour is not None else None)
        return rows

    def _effect_from_form(
        self, profile: Any, stored: EffectSettings, user_input: dict[str, Any]
    ) -> EffectSettings:
        """Turn the submitted effect panel into settings.

        Rows are read in order up to the effect's cap. A row left empty is
        skipped rather than treated as black, so clearing the second colour of
        a gradient leaves a one-stop gradient instead of a black stop.
        """
        colours: list[tuple[int, int, int, int]] = []
        rows = self._panel_rows or profile.panel_colours
        draft = self._draft_rows
        for slot in range(min(rows, profile.max_colours)):
            colour = _colour_of(
                user_input.get(f"color_{slot + 1}"),
                user_input.get(f"white_{slot + 1}"),
            )
            if colour is None and draft is not None and slot < len(draft):
                # An untouched row keeps whatever the round trip carried over.
                stored_row = draft[slot]
                colour = (
                    (stored_row[0], stored_row[1], stored_row[2], stored_row[3])
                    if stored_row
                    else None
                )
            if colour is not None:
                colours.append(colour)

        if profile.palette is PaletteMode.GENERATED and not colours:
            # The effect paints its own colours. Keep whatever was stored so
            # switching back to a palette effect is not a surprise, and because
            # the wire still needs one valid colour.
            colours = list(stored.colors)

        return EffectSettings(
            colors=tuple(colours),
            speed=(
                _bounded(user_input["speed"], 0, 100)
                if profile.speed and "speed" in user_input
                else stored.speed
            ),
            density=(
                _bounded(user_input["density"], 0, 100)
                if profile.density and "density" in user_input
                else stored.density
            ),
            direction=(
                (1 if user_input.get("direction", "l2r") == "l2r" else 2)
                if profile.direction
                else stored.direction
            ),
        )

    # -- looks -------------------------------------------------------------

    async def async_step_look(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which per-LED look to configure."""
        if user_input is not None:
            self._pending_look = user_input["look"]
            return await self.async_step_look_settings()

        options = []
        for look in LOOK_LIST:
            if look.supports_palette:
                count = look.palette_size
                hint = f"{count} colour{'s' if count != 1 else ''}"
            else:
                hint = "fixed colours"
            configured = (
                " · saved"
                if look_settings(self._options, look.name) is not None
                else ""
            )
            options.append(
                SelectOptionDict(
                    value=look.name,
                    label=f"{look.title} — {look.description} ({hint}){configured}",
                )
            )

        return self.async_show_form(
            step_id=STEP_LOOK,
            data_schema=vol.Schema(
                {
                    vol.Required("look", default=next(iter(LOOKS))): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
            description_placeholders={
                "count": str(self.config_entry.data.get(CONF_LED_COUNT, 35))
            },
        )

    async def async_step_look_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The generated panel for one per-LED look."""
        name = self._pending_look
        if name is None:  # defensive
            return await self.async_step_look()
        look = LOOKS[name]
        stored = look_settings(self._options, name) or LookSettings()

        if user_input is not None:
            settings = self._look_from_form(look, stored, user_input)
            options = with_look(self._options, name, settings)
            await self._apply_look(name, settings, options)
            self._pending_look = None
            return self.async_create_entry(title="", data=options)

        fields: dict[Any, Any] = {}
        slots = look.palette_size if look.supports_palette else 0
        for slot in range(slots):
            default = (
                stored.palette[slot]
                if slot < len(stored.palette)
                else (*look.palette[slot][:3], 0)
            )
            fields[vol.Optional(f"color_{slot + 1}", default=default[:3])] = (
                ColorRGBSelector()
            )
            fields[vol.Optional(f"white_{slot + 1}", default=default[3])] = _slider()

        fields[vol.Optional("scribble_effect", default=str(stored.effect))] = (
            SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=str(value), label=label)
                        for value, label in sorted(SCRIBBLE_EFFECT_NAMES.items())
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        )
        fields[vol.Optional("speed", default=stored.speed)] = _slider()
        fields[vol.Optional("density", default=stored.density)] = _slider()
        fields[
            vol.Optional("direction", default="l2r" if stored.direction == 1 else "r2l")
        ] = _direction_selector()

        return self.async_show_form(
            step_id=STEP_LOOK_SETTINGS,
            data_schema=vol.Schema(fields),
            description_placeholders={
                "look": look.title,
                "description": look.description,
            },
        )

    def _look_from_form(
        self, look: Any, stored: LookSettings, user_input: dict[str, Any]
    ) -> LookSettings:
        """Turn the submitted look panel into settings."""
        colours: list[tuple[int, int, int, int]] = []
        slots = look.palette_size if look.supports_palette else 0
        for slot in range(slots):
            colour = _colour_of(
                user_input.get(f"color_{slot + 1}"),
                user_input.get(f"white_{slot + 1}"),
            )
            if colour is not None:
                colours.append(colour)
        return LookSettings(
            palette=tuple(colours) or stored.palette,
            speed=_bounded(user_input["speed"], 0, 100),
            density=_bounded(user_input["density"], 0, 100),
            direction=1 if user_input["direction"] == "l2r" else 2,
            effect=_bounded(user_input["scribble_effect"], 0, 8),
        )

    # -- applying ----------------------------------------------------------

    def _coordinator(self) -> Any:
        """The live coordinator for this entry, if it is set up."""
        container = self.hass.data.get(DOMAIN)
        if not container:
            return None
        return container.get(self.config_entry.entry_id)

    async def _apply_effect(
        self,
        name: str,
        settings: EffectSettings,
        options: dict[str, Any],
    ) -> None:
        """Push the panel's values to the strip, so saving is also applying.

        ``options`` is the exact dict the flow is about to save, passed in rather
        than rebuilt, so the claim can never differ from the save by a key and
        silently let the listener re-apply.

        The save dispatches the entry's update listener eagerly, and that
        listener re-applies whatever effect is showing -- which this call has
        *already* done. Claiming the options suppresses the repeat, so an
        already-correct strip does not flash twice.

        Failures are swallowed: the settings are still worth saving when the
        controller happens to be offline, and the user can apply them later.
        Nothing is claimed after a failure, so the listener's best-effort retry
        still runs.
        """
        coordinator = self._coordinator()
        if coordinator is None:
            return
        try:
            await coordinator.async_set_effect(
                name,
                colors=list(settings.colors) or None,
                speed=settings.speed,
                density=settings.density,
                direction=settings.direction,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("could not apply effect %s after saving: %s", name, err)
            return
        coordinator.expect_no_reapply(options)

    async def _apply_look(
        self,
        name: str,
        settings: LookSettings,
        options: dict[str, Any],
    ) -> None:
        """Render the panel's values, so saving is also applying.

        Same reasoning as :meth:`_apply_effect`: this render is the one the
        update listener would otherwise repeat.
        """
        coordinator = self._coordinator()
        if coordinator is None:
            return
        try:
            await coordinator.async_set_look(
                name,
                palette=[list(colour) for colour in settings.palette] or None,
                speed=settings.speed,
                density=settings.density,
                direction=settings.direction,
                effect=settings.effect,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("could not apply look %s after saving: %s", name, err)
            return
        coordinator.expect_no_reapply(options)
