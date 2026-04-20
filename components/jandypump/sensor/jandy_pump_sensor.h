#pragma once

#include "esphome/components/jandypump/jandy_pump.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/core/component.h"

namespace esphome {
namespace jandy_pump {

class JandyPumpSensor : public JandyPumpItemBase, public Component, public sensor::Sensor {
 public:
  JandyPumpSensor(uint8_t sensor_addr, uint16_t scale)
      : JandyPumpItemBase(), sensor_addr_(sensor_addr), scale_(scale) {}

  JandyPumpCommand create_command() override;

 private:
  uint8_t sensor_addr_;
  uint16_t scale_;
};

}  // namespace jandy_pump
}  // namespace esphome
