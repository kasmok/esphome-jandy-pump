import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import uart
from esphome.const import CONF_ID
from esphome import pins

from .const import CONF_JANDY_PUMP_ID, CONF_FLOW_CONTROL_PIN

CODEOWNERS = ["@kasmok"]

DEPENDENCIES = ["uart"]
AUTO_LOAD = ["sensor", "switch", "number"]

MULTI_CONF = True

jandy_pump_ns = cg.esphome_ns.namespace("jandy_pump")
JandyPump = jandy_pump_ns.class_("JandyPump", cg.PollingComponent, uart.UARTDevice)

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(JandyPump),
            cv.Optional(CONF_FLOW_CONTROL_PIN): pins.gpio_output_pin_schema,
        }
    )
    .extend(cv.polling_component_schema("2s"))
    .extend(uart.UART_DEVICE_SCHEMA)
)

JandyPumpItemSchema = cv.Schema(
    {
        cv.GenerateID(CONF_JANDY_PUMP_ID): cv.use_id(JandyPump),
    }
)


async def add_jandy_pump_base_properties(var, config, item_type):
    pass


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)

    if CONF_FLOW_CONTROL_PIN in config:
        pin = await cg.gpio_pin_expression(config[CONF_FLOW_CONTROL_PIN])
        cg.add(var.set_flow_control_pin(pin))
