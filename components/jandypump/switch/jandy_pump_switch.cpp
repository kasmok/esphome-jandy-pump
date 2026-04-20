#include "jandy_pump_switch.h"

namespace esphome {
namespace jandy_pump {

static const char *const TAG = "jandy_pump.switch";

JandyPumpCommand JandyPumpSwitch::create_command() {
  return JandyPumpCommand::create_status_command(
      pump_,
      [this](JandyPump *pump, bool running) {
        this->publish_state(running);
      });
}

void JandyPumpSwitch::write_state(bool state) {
  if (state) {
    pump_->queue_command_(JandyPumpCommand::create_run_command(
        pump_,
        [this](JandyPump *pump) { this->publish_state(true); }));
  } else {
    pump_->queue_command_(JandyPumpCommand::create_stop_command(
        pump_,
        [this](JandyPump *pump) { this->publish_state(false); }));
  }
  this->publish_state(state);
  pump_->update();
}

}  // namespace jandy_pump
}  // namespace esphome
