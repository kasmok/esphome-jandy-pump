#pragma once

#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/components/uart/uart.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/switch/switch.h"

#include <queue>
#include <list>
#include <functional>

/*
    Jandy DLE-framed protocol for VSFloPro / ePump variable-speed pumps.

    Packet format:
      10 02  [addr] [func] [data...] [checksum]  10 03
    Checksum = sum(0x10, 0x02, addr, func, data...) & 0xFF
    Escape:  literal 0x10 in data → transmitted as 10 10

    Addresses:
      0x78 = command destination (to pump)
      0x1F = pump response (status/sensor/demand)
      0x20 = pump response (identification/config)
      0x01 = pump response (go/stop ack)
*/

namespace esphome {
namespace jandy_pump {

static const uint8_t JANDY_PUMP_ADDR = 0x78;
static const uint8_t JANDY_ADDR_NACK = 0xFF;
static const uint8_t JANDY_FUNC_GO = 0x41;
static const uint8_t JANDY_FUNC_STOP = 0x42;
static const uint8_t JANDY_FUNC_STATUS = 0x43;
static const uint8_t JANDY_FUNC_SET_DEMAND = 0x44;
static const uint8_t JANDY_FUNC_READ_SENSOR = 0x45;
static const uint8_t JANDY_FUNC_READ_ID = 0x46;
static const uint8_t JANDY_FUNC_CONFIG = 0x64;

static const uint8_t JANDY_STATUS_STOPPED = 0x00;
static const uint8_t JANDY_STATUS_BOOT = 0x09;
static const uint8_t JANDY_STATUS_RUNNING = 0x0B;
static const uint8_t JANDY_STATUS_FAULT = 0x20;

class JandyPump;

/////////////////////////////////////////////////////////////////////////////////////////////////
class JandyPumpCommand {
 public:
  static const uint8_t MAX_SEND_REPEATS = 5;

  JandyPump *pump_{};
  uint8_t function_{};
  std::vector<uint8_t> payload_ = {};
  std::function<void(JandyPump *pump, const std::vector<uint8_t> &data)> on_data_func_;
  uint8_t send_countdown{MAX_SEND_REPEATS};

  bool send();

  static JandyPumpCommand create_status_command(
      JandyPump *pump,
      std::function<void(JandyPump *pump, bool running)> on_status_func);

  static JandyPumpCommand create_read_sensor_command(
      JandyPump *pump, uint8_t sensor_addr, uint16_t scale,
      std::function<void(JandyPump *pump, uint16_t value)> on_value_func);

  static JandyPumpCommand create_run_command(
      JandyPump *pump,
      std::function<void(JandyPump *pump)> on_confirmation_func);

  static JandyPumpCommand create_stop_command(
      JandyPump *pump,
      std::function<void(JandyPump *pump)> on_confirmation_func);

  static JandyPumpCommand create_set_demand_command(
      JandyPump *pump, uint16_t rpm,
      std::function<void(JandyPump *pump)> on_confirmation_func);
};

/////////////////////////////////////////////////////////////////////////////////////////////////
class JandyPumpItemBase {
 public:
  JandyPumpItemBase() : pump_(nullptr) {}
  JandyPumpItemBase(JandyPump *pump) : pump_(pump) {}
  virtual JandyPumpCommand create_command() = 0;
  void set_pump(JandyPump *pump) { pump_ = pump; }

 protected:
  JandyPump *pump_;
};

/////////////////////////////////////////////////////////////////////////////////////////////////
class JandyPump : public PollingComponent, public uart::UARTDevice {
 public:
  JandyPump() {}

  void set_flow_control_pin(GPIOPin *pin) { flow_control_pin_ = pin; }

  void loop() override;
  void setup() override;
  void update() override;
  void dump_config() override;

  void add_item(JandyPumpItemBase *item) { items_.push_back(item); }
  void queue_command_(const JandyPumpCommand &cmd);

  // Send a raw Jandy DLE-framed packet: [addr] [func] [data...]
  void send_jandy_raw(const std::vector<uint8_t> &payload);

 protected:
  void process_rx_byte_(uint8_t byte);
  void process_rx_packet_(const std::vector<uint8_t> &packet);
  bool send_next_command_();
  void queue_init_sequence_();

 private:
  GPIOPin *flow_control_pin_{nullptr};

  // Command/response queues (same pattern as CenturyVSPump)
  std::list<std::unique_ptr<JandyPumpCommand>> command_queue_;
  std::queue<std::unique_ptr<JandyPumpCommand>> response_queue_;
  uint32_t last_command_timestamp_{0};
  uint16_t command_throttle_{50};  // ms between commands

  // RX state machine for DLE framing
  enum RxState { RX_IDLE, RX_DLE_START, RX_DATA, RX_DLE_ESCAPE };
  RxState rx_state_{RX_IDLE};
  std::vector<uint8_t> rx_buffer_;
  uint32_t rx_last_byte_time_{0};
  bool waiting_for_response_{false};
  uint32_t response_timeout_{500};  // ms

  // Initialization state — pump requires ReadID/Config handshake before
  // it will accept Set Demand or Read Sensor commands
  bool initialized_{false};

 public:
  std::vector<JandyPumpItemBase *> items_;
};

}  // namespace jandy_pump
}  // namespace esphome
