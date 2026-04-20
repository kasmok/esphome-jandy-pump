from esphome.components import sensor
import esphome.config_validation as cv
import esphome.codegen as cg

from esphome.const import CONF_ID, CONF_ADDRESS, CONF_TYPE

from .. import (
    add_jandy_pump_base_properties,
    jandy_pump_ns,
    JandyPumpItemSchema,
)
from ..const import CONF_JANDY_PUMP_ID

DEPENDENCIES = ["jandypump"]
CODEOWNERS = ["@kasmok"]

JandyPumpSensor = jandy_pump_ns.class_(
    "JandyPumpSensor", cg.Component, sensor.Sensor
)

SENSOR_TYPES = {
    "rpm": {"address": 0x00, "scale": 4},
    "watts": {"address": 0x0A, "scale": 1},
    "custom": {},
}

CONF_SCALE = "scale"

CONFIG_SCHEMA = cv.All(
    sensor.sensor_schema(JandyPumpSensor)
    .extend(cv.COMPONENT_SCHEMA)
    .extend(JandyPumpItemSchema)
    .extend(
        {
            cv.GenerateID(): cv.declare_id(JandyPumpSensor),
            cv.Required(CONF_TYPE): cv.one_of(*SENSOR_TYPES, lower=True),
            cv.Optional(CONF_ADDRESS, default=0): cv.positive_int,
            cv.Optional(CONF_SCALE, default=1): cv.positive_int,
        }
    ),
)


async def to_code(config):
    sensor_type = config[CONF_TYPE]
    if sensor_type in SENSOR_TYPES and SENSOR_TYPES[sensor_type]:
        config[CONF_ADDRESS] = SENSOR_TYPES[sensor_type]["address"]
        config[CONF_SCALE] = SENSOR_TYPES[sensor_type]["scale"]

    var = cg.new_Pvariable(
        config[CONF_ID],
        config[CONF_ADDRESS],
        config[CONF_SCALE],
    )
    await cg.register_component(var, config)
    await sensor.register_sensor(var, config)

    paren = await cg.get_variable(config[CONF_JANDY_PUMP_ID])
    cg.add(var.set_pump(paren))
    cg.add(paren.add_item(var))
    await add_jandy_pump_base_properties(var, config, JandyPumpSensor)
