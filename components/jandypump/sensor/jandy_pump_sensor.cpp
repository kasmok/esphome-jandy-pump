#include "jandy_pump_sensor.h"

namespace esphome {
namespace jandy_pump {

static const char *const TAG = "jandy_pump.sensor";

JandyPumpCommand JandyPumpSensor::create_command() {
  return JandyPumpCommand::create_read_sensor_command(
      pump_, sensor_addr_, scale_,
      [=](JandyPump *pump, uint16_t value) {
        this->publish_state((float)value);
      });
}

}  // namespace jandy_pump
}  // namespace esphome
