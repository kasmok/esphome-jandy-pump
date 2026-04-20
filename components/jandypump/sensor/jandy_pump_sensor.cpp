#include "jandy_pump_sensor.h"

namespace esphome {
namespace jandy_pump {

static const char *const TAG = "jandy_pump.sensor";

JandyPumpCommand JandyPumpSensor::create_command() {
  return JandyPumpCommand::create_read_sensor_command(
      pump_, sensor_addr_, scale_,
      [this](JandyPump *pump, uint16_t raw_value) {
        float value = (float)raw_value / (float)scale_;
        this->publish_state(value);
      });
}

}  // namespace jandy_pump
}  // namespace esphome
