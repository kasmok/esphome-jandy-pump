from esphome.components import switch
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

JandyPumpSwitch = jandy_pump_ns.class_(
    "JandyPumpSwitch", cg.Component, switch.Switch
)

CONFIG_SCHEMA = cv.All(
    switch.switch_schema(JandyPumpSwitch)
    .extend(cv.COMPONENT_SCHEMA)
    .extend(JandyPumpItemSchema)
    .extend(
        {
            cv.GenerateID(): cv.declare_id(JandyPumpSwitch),
        }
    ),
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await switch.register_switch(var, config)

    paren = await cg.get_variable(config[CONF_JANDY_PUMP_ID])
    cg.add(var.set_pump(paren))
    cg.add(paren.add_item(var))
    await add_jandy_pump_base_properties(var, config, JandyPumpSwitch)
