#pragma once

#include "esphome/components/jandypump/jandy_pump.h"
#include "esphome/components/switch/switch.h"
#include "esphome/core/component.h"

namespace esphome {
namespace jandy_pump {

class JandyPumpSwitch : public JandyPumpItemBase, public Component, public switch_::Switch {
 public:
  JandyPumpSwitch() : JandyPumpItemBase() {}

  void write_state(bool state) override;
  JandyPumpCommand create_command() override;
};

}  // namespace jandy_pump
}  // namespace esphome
