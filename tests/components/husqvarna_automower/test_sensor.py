"""Tests for sensor platform."""

import datetime
from unittest.mock import AsyncMock, patch
import zoneinfo

from aioautomower.model import (
    ExternalReasons,
    MowerAttributes,
    MowerModes,
    MowerStates,
    RestrictedReasons,
)
from freezegun.api import FrozenDateTimeFactory
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.husqvarna_automower.const import DOMAIN
from homeassistant.components.husqvarna_automower.coordinator import SCAN_INTERVAL
from homeassistant.const import STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .const import TEST_MOWER_ID

from tests.common import MockConfigEntry, async_fire_time_changed, snapshot_platform

# Work-area IDs used in tests/components/husqvarna_automower/fixtures/mower.json.
# 123456 -> "Front lawn", type SYSTEMATIC
#    0   -> "" (deserialized to "my_lawn"), type SYSTEMATIC
# 654321 -> "Back lawn",  type RANDOM (no progress / last_time_completed)
SYSTEMATIC_WORK_AREA_ID = 123456


async def test_sensor_unknown_states(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    values: dict[str, MowerAttributes],
) -> None:
    """Test a sensor which returns unknown."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get("sensor.test_mower_1_mode")
    assert state is not None
    assert state.state == "main_area"

    values[TEST_MOWER_ID].mower.mode = MowerModes.UNKNOWN
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_mower_1_mode")
    assert state.state == STATE_UNAVAILABLE


async def test_cutting_blade_usage_time_sensor(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test if this sensor is only added, if data is available."""

    await setup_integration(hass, mock_config_entry)
    state = hass.states.get("sensor.test_mower_1_cutting_blade_usage_time")
    assert state is not None
    assert float(state.state) == pytest.approx(0.03416666)


@pytest.mark.freeze_time(
    datetime.datetime(2023, 6, 5, tzinfo=zoneinfo.ZoneInfo("Europe/Berlin"))
)
async def test_next_start_sensor(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    values: dict[str, MowerAttributes],
) -> None:
    """Test if this sensor is only added, if data is available."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get("sensor.test_mower_1_next_start")
    assert state is not None
    assert state.state == "2023-06-05T17:00:00+00:00"

    values[TEST_MOWER_ID].planner.next_start_datetime = None
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_mower_1_next_start")
    assert state.state == STATE_UNAVAILABLE


async def test_work_area_sensor(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    values: dict[str, MowerAttributes],
) -> None:
    """Test the work area sensor."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get("sensor.test_mower_1_work_area")
    assert state is not None
    assert state.state == "Front lawn"

    values[TEST_MOWER_ID].mower.work_area_id = None
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_mower_1_work_area")
    assert state.state == "no_work_area_active"

    values[TEST_MOWER_ID].mower.work_area_id = 0
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_mower_1_work_area")
    assert state.state == "my_lawn"

    # Test EPOS mower, which returns work_area_id = 0, when no
    # work area is active and has no default work_area_id=0
    values[TEST_MOWER_ID].mower.work_area_id = 0
    del values[TEST_MOWER_ID].work_areas[0]
    del values[TEST_MOWER_ID].work_area_dict[0]
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.test_mower_1_work_area")
    assert state.state == "no_work_area_active"


async def test_work_area_progress_sensors_gated_by_type(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Progress and last_time_completed sensors only exist for SYSTEMATIC areas.

    Random work areas never report ``progress`` / ``lastTimeCompleted`` from
    the Husqvarna API, so the corresponding sensors must not be created. The
    setup-time gate is on ``WorkArea.type == SYSTEMATIC`` (stable, capability-
    derived) instead of on the value being ``None`` (transient), which avoids
    the previous registry-restore zombie state for systematic areas where the
    value briefly went ``None`` between completions.
    """
    await setup_integration(hass, mock_config_entry)

    # Front lawn (SYSTEMATIC, work-area id 123456) -> entities are created.
    assert hass.states.get("sensor.test_mower_1_front_lawn_progress") is not None
    assert (
        hass.states.get("sensor.test_mower_1_front_lawn_last_time_completed")
        is not None
    )

    # Back lawn (RANDOM, work-area id 654321) -> entities are filtered out at
    # setup time, regardless of whether the API populates progress later.
    assert hass.states.get("sensor.test_mower_1_back_lawn_progress") is None
    assert hass.states.get("sensor.test_mower_1_back_lawn_last_time_completed") is None


async def test_work_area_systematic_sensor_unavailable_without_value(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    values: dict[str, MowerAttributes],
) -> None:
    """A SYSTEMATIC area with a transient ``None`` value reports unavailable.

    Regression: previously the value-based ``exists_fn`` filter would drop the
    entity at setup whenever ``progress``/``last_time_completed`` was briefly
    ``None``, leaving the entity-registry entry behind as a permanent
    ``restored: True`` zombie. With the type-based gate the entity is
    registered as long as the area exists; while the value is ``None`` the
    standard ``available`` property returns ``False`` and the sensor reports
    ``unavailable`` cleanly, then recovers to a concrete value once the API
    reports one.
    """
    values[TEST_MOWER_ID].work_areas[SYSTEMATIC_WORK_AREA_ID].last_time_completed = None
    mock_automower_client.get_status.return_value = values

    await setup_integration(hass, mock_config_entry)

    sensor_id = "sensor.test_mower_1_front_lawn_last_time_completed"
    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    values[TEST_MOWER_ID].work_areas[
        SYSTEMATIC_WORK_AREA_ID
    ].last_time_completed = datetime.datetime(
        2024, 10, 1, 11, 11, 0, tzinfo=zoneinfo.ZoneInfo("Europe/Berlin")
    )
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(sensor_id)
    assert state is not None
    assert state.state == "2024-10-01T09:11:00+00:00"


async def test_work_area_sensor_restores_from_entity_registry(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    values: dict[str, MowerAttributes],
) -> None:
    """Pre-existing registry entries must be reused, not duplicated, on setup.

    Regression for the original bug: a previous integration version had
    registered ``progress`` / ``last_time_completed`` sensors for a SYSTEMATIC
    work area; on a later reload the value-based ``exists_fn`` filter dropped
    the entity at setup, leaving the registry entry behind as a permanent
    ``restored: True`` zombie that never recovered.

    This test exercises that restore path directly:

    1. Pre-create entity-registry entries for the SYSTEMATIC ``Front lawn``
       progress and last_time_completed sensors (mirroring what a previous
       setup would have left behind).
    2. Force the coordinator to report ``None`` for both values, simulating
       the brief window in which the original filter would have dropped the
       entities.
    3. Run setup. The pre-existing registry entries must be reused (no
       ``_2`` suffix) and the entities must register cleanly as
       ``unavailable`` instead of staying as restored zombies.
    4. Once the API reports concrete values again, the same entity ids must
       transition to those values without a reload.
    """
    progress_entity_id = "sensor.test_mower_1_front_lawn_progress"
    last_time_entity_id = "sensor.test_mower_1_front_lawn_last_time_completed"
    progress_unique_id = f"{TEST_MOWER_ID}_{SYSTEMATIC_WORK_AREA_ID}_progress"
    last_time_unique_id = (
        f"{TEST_MOWER_ID}_{SYSTEMATIC_WORK_AREA_ID}_last_time_completed"
    )

    mock_config_entry.add_to_hass(hass)

    progress_entry = entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=progress_unique_id,
        suggested_object_id="test_mower_1_front_lawn_progress",
        config_entry=mock_config_entry,
    )
    last_time_entry = entity_registry.async_get_or_create(
        domain="sensor",
        platform=DOMAIN,
        unique_id=last_time_unique_id,
        suggested_object_id="test_mower_1_front_lawn_last_time_completed",
        config_entry=mock_config_entry,
    )
    assert progress_entry.entity_id == progress_entity_id
    assert last_time_entry.entity_id == last_time_entity_id

    values[TEST_MOWER_ID].work_areas[SYSTEMATIC_WORK_AREA_ID].progress = None
    values[TEST_MOWER_ID].work_areas[SYSTEMATIC_WORK_AREA_ID].last_time_completed = None
    mock_automower_client.get_status.return_value = values

    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Same entity ids reused, no duplicates with a ``_2`` suffix.
    assert entity_registry.async_get(progress_entity_id) is not None
    assert entity_registry.async_get(f"{progress_entity_id}_2") is None
    assert entity_registry.async_get(last_time_entity_id) is not None
    assert entity_registry.async_get(f"{last_time_entity_id}_2") is None

    # Both sensors are registered but unavailable while values are ``None``.
    progress_state = hass.states.get(progress_entity_id)
    last_time_state = hass.states.get(last_time_entity_id)
    assert progress_state is not None
    assert progress_state.state == STATE_UNAVAILABLE
    assert last_time_state is not None
    assert last_time_state.state == STATE_UNAVAILABLE

    # Coordinator now reports concrete values; entities recover without
    # reload or re-setup.
    values[TEST_MOWER_ID].work_areas[SYSTEMATIC_WORK_AREA_ID].progress = 42
    values[TEST_MOWER_ID].work_areas[
        SYSTEMATIC_WORK_AREA_ID
    ].last_time_completed = datetime.datetime(
        2024, 10, 1, 11, 11, 0, tzinfo=zoneinfo.ZoneInfo("Europe/Berlin")
    )
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    progress_state = hass.states.get(progress_entity_id)
    last_time_state = hass.states.get(last_time_entity_id)
    assert progress_state is not None
    assert progress_state.state == "42"
    assert last_time_state is not None
    assert last_time_state.state == "2024-10-01T09:11:00+00:00"


async def test_restricted_reason_sensor(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    values: dict[str, MowerAttributes],
) -> None:
    """Test the work area sensor."""
    sensor = "sensor.test_mower_1_restricted_reason"
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(sensor)
    assert state is not None
    assert state.state == RestrictedReasons.WEEK_SCHEDULE

    values[TEST_MOWER_ID].planner.restricted_reason = RestrictedReasons.EXTERNAL
    values[TEST_MOWER_ID].planner.external_reason = None
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get(sensor)
    assert state.state == RestrictedReasons.EXTERNAL

    values[TEST_MOWER_ID].planner.restricted_reason = RestrictedReasons.EXTERNAL
    values[
        TEST_MOWER_ID
    ].planner.external_reason = ExternalReasons.SMART_ROUTINE_WILDLIFE_PROTECTION
    mock_automower_client.get_status.return_value = values
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get(sensor)
    assert state.state == ExternalReasons.SMART_ROUTINE_WILDLIFE_PROTECTION


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
@pytest.mark.parametrize(
    ("sensor_to_test"),
    [
        ("cutting_blade_usage_time"),
        ("number_of_charging_cycles"),
        ("number_of_collisions"),
        ("total_charging_time"),
        ("total_cutting_time"),
        ("total_running_time"),
        ("total_searching_time"),
        ("total_drive_distance"),
    ],
)
async def test_statistics_not_available(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    sensor_to_test: str,
    values: dict[str, MowerAttributes],
) -> None:
    """Test if this sensor is only added, if data is available."""

    delattr(values[TEST_MOWER_ID].statistics, sensor_to_test)
    mock_automower_client.get_status.return_value = values
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(f"sensor.test_mower_1_{sensor_to_test}")
    assert state is None


async def test_error_sensor(
    hass: HomeAssistant,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    values: dict[str, MowerAttributes],
) -> None:
    """Test error sensor."""
    await setup_integration(hass, mock_config_entry)

    for state, error_key, expected_state in (
        (MowerStates.IN_OPERATION, None, "no_error"),
        (MowerStates.ERROR, "can_error", "can_error"),
        (MowerStates.ERROR, None, MowerStates.ERROR.lower()),
        (MowerStates.ERROR_AT_POWER_UP, None, MowerStates.ERROR_AT_POWER_UP.lower()),
        (MowerStates.FATAL_ERROR, None, MowerStates.FATAL_ERROR.lower()),
    ):
        values[TEST_MOWER_ID].mower.state = state
        values[TEST_MOWER_ID].mower.error_key = error_key
        mock_automower_client.get_status.return_value = values
        freezer.tick(SCAN_INTERVAL)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        state = hass.states.get("sensor.test_mower_1_error")
        assert state.state == expected_state


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_sensor_snapshot(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_automower_client: AsyncMock,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Snapshot test of the sensors."""
    with patch(
        "homeassistant.components.husqvarna_automower.PLATFORMS",
        [Platform.SENSOR],
    ):
        await setup_integration(hass, mock_config_entry)
        await snapshot_platform(
            hass, entity_registry, snapshot, mock_config_entry.entry_id
        )
