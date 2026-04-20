#include "jandy_pump_number.h"

namespace esphome {
namespace jandy_pump {

static const char *const TAG = "jandy_pump.number";

JandyPumpCommand JandyPumpNumber::create_command() {
  // On each poll cycle, read the current demand from the pump (sensor addr 0x03)
  return JandyPumpCommand::create_read_sensor_command(
      pump_, 0x03, 4,  // sensor_addr=0x03 (Demand), scale=4
      [=](JandyPump *pump, uint16_t value) {
        this->publish_state((float)value);
      });
}

void JandyPumpNumber::control(float value) {
  uint16_t rpm = (uint16_t)value;
  ESP_LOGD(TAG, "Setting demand to %d RPM", rpm);
  pump_->queue_command_(JandyPumpCommand::create_set_demand_command(
      pump_, rpm,
      [=](JandyPump *pump) {
        this->publish_state(value);
      }));
  this->publish_state(value);
  pump_->update();
}

}  // namespace jandy_pump
}  // namespace esphome
