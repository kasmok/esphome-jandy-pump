#pragma once

#include "esphome/components/jandypump/jandy_pump.h"
#include "esphome/components/number/number.h"
#include "esphome/core/component.h"

namespace esphome {
namespace jandy_pump {

class JandyPumpNumber : public JandyPumpItemBase, public Component, public number::Number {
 public:
  JandyPumpNumber() : JandyPumpItemBase() {}

  JandyPumpCommand create_command() override;
  void control(float value) override;
};

}  // namespace jandy_pump
}  // namespace esphome
