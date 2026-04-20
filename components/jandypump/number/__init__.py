from esphome.components import number
import esphome.config_validation as cv
import esphome.codegen as cg

from esphome.const import CONF_ID

from .. import (
    add_jandy_pump_base_properties,
    jandy_pump_ns,
    JandyPumpItemSchema,
)
from ..const import CONF_JANDY_PUMP_ID

DEPENDENCIES = ["jandypump"]
CODEOWNERS = ["@kasmok"]

JandyPumpNumber = jandy_pump_ns.class_(
    "JandyPumpNumber", cg.Component, number.Number
)

CONFIG_SCHEMA = cv.All(
    number.number_schema(JandyPumpNumber)
    .extend(cv.COMPONENT_SCHEMA)
    .extend(JandyPumpItemSchema)
    .extend(
        {
            cv.GenerateID(): cv.declare_id(JandyPumpNumber),
        }
    ),
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await number.register_number(var, config, min_value=600, max_value=3450, step=50)

    paren = await cg.get_variable(config[CONF_JANDY_PUMP_ID])
    cg.add(var.set_pump(paren))
    cg.add(paren.add_item(var))
    await add_jandy_pump_base_properties(var, config, JandyPumpNumber)
