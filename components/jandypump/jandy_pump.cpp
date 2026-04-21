#include "jandy_pump.h"
#include "esphome/core/log.h"

namespace esphome {
namespace jandy_pump {

static const char *const TAG = "jandy_pump";

/////////////////////////////////////////////////////////////////////////////////////////////
void JandyPump::setup() {
  if (this->flow_control_pin_ != nullptr) {
    this->flow_control_pin_->setup();
    this->flow_control_pin_->digital_write(false);  // RX mode
  }
  ESP_LOGCONFIG(TAG, "Jandy pump setup complete");
}

/////////////////////////////////////////////////////////////////////////////////////////////
void JandyPump::dump_config() {
  ESP_LOGCONFIG(TAG, "Jandy Pump (DLE protocol):");
  ESP_LOGCONFIG(TAG, "  Pump address: 0x%02X", JANDY_PUMP_ADDR);
  if (this->flow_control_pin_ != nullptr) {
    LOG_PIN("  Flow Control Pin: ", this->flow_control_pin_);
  }
}

/////////////////////////////////////////////////////////////////////////////////////////////
void JandyPump::loop() {
  // Read all available UART bytes
  while (this->available()) {
    uint8_t byte;
    this->read_byte(&byte);
    this->rx_last_byte_time_ = millis();
    this->process_rx_byte_(byte);
  }

  // Check for response timeout
  if (this->waiting_for_response_) {
    if (millis() - this->rx_last_byte_time_ > this->response_timeout_) {
      ESP_LOGD(TAG, "Response timeout");
      this->waiting_for_response_ = false;
      this->rx_state_ = RX_IDLE;
      this->rx_buffer_.clear();
    }
  }

  // Process received responses
  if (!response_queue_.empty()) {
    auto &message = response_queue_.front();
    if (message != nullptr && message->on_data_func_) {
      message->on_data_func_(this, message->payload_);
    }
    response_queue_.pop();
  } else {
    send_next_command_();
  }
}

/////////////////////////////////////////////////////////////////////////////////////////////
void JandyPump::update() {
  // The original controller sends ReadID + Config on every poll cycle.
  // ReadID appears to be the key that authorizes this controller to the pump —
  // without it, Read Sensor and Set Demand get NACK 0x03, and Config is ignored.
  //
  // Sequence per cycle (matching original capture):
  //   ReadID page 3 → Config page 6 → Status → [sensors...]

  // ReadID page 3 — queued as normal command so we wait for the response
  JandyPumpCommand readid_cmd = {};
  readid_cmd.pump_ = this;
  readid_cmd.function_ = JANDY_FUNC_READ_ID;
  readid_cmd.payload_ = {0x03};
  readid_cmd.send_countdown = 1;
  readid_cmd.on_data_func_ = [](JandyPump *pump, const std::vector<uint8_t> data) {
    ESP_LOGI(TAG, "ReadID response: %d bytes", data.size());
  };
  queue_command_(readid_cmd);

  // Config page 6 — fire-and-forget with checksum+5 (matching original controller)
  send_fire_and_forget_(JANDY_FUNC_CONFIG, {0x06}, 5);

  for (auto item : items_)
    queue_command_(item->create_command());
}

/////////////////////////////////////////////////////////////////////////////////////////////
void JandyPump::queue_command_(const JandyPumpCommand &command) {
  command_queue_.push_back(make_unique<JandyPumpCommand>(command));
}

/////////////////////////////////////////////////////////////////////////////////////////////
void JandyPump::send_jandy_raw(const std::vector<uint8_t> &payload, uint8_t cs_offset) {
  // Compute checksum: sum(0x10, 0x02, payload_bytes...) & 0xFF
  // cs_offset adds to checksum (5 for Config commands — original controller quirk)
  uint8_t checksum = 0x10 + 0x02;
  for (auto b : payload) {
    checksum += b;
  }
  checksum = (checksum + cs_offset) & 0xFF;

  // Build the wire frame with DLE escaping
  std::vector<uint8_t> frame;
  frame.push_back(0x10);  // DLE
  frame.push_back(0x02);  // STX

  for (auto b : payload) {
    frame.push_back(b);
    if (b == 0x10)
      frame.push_back(0x10);  // DLE escape
  }

  // Checksum (also needs escaping)
  frame.push_back(checksum);
  if (checksum == 0x10)
    frame.push_back(0x10);

  frame.push_back(0x10);  // DLE
  frame.push_back(0x03);  // ETX

  // Switch to TX mode
  if (this->flow_control_pin_ != nullptr)
    this->flow_control_pin_->digital_write(true);

  this->write_array(frame);
  this->flush();

  // Switch back to RX mode
  if (this->flow_control_pin_ != nullptr)
    this->flow_control_pin_->digital_write(false);

  // Log the sent packet
  std::string hex_str;
  for (auto b : frame) {
    char buf[4];
    snprintf(buf, sizeof(buf), "%02X ", b);
    hex_str += buf;
  }
  ESP_LOGD(TAG, "TX: %s", hex_str.c_str());

  this->waiting_for_response_ = true;
  this->rx_last_byte_time_ = millis();
}

/////////////////////////////////////////////////////////////////////////////////////////////
// DLE framing RX state machine
void JandyPump::process_rx_byte_(uint8_t byte) {
  switch (this->rx_state_) {
    case RX_IDLE:
      if (byte == 0x10) {
        this->rx_state_ = RX_DLE_START;
      }
      break;

    case RX_DLE_START:
      if (byte == 0x02) {
        // DLE STX — start of frame
        this->rx_buffer_.clear();
        this->rx_state_ = RX_DATA;
      } else {
        this->rx_state_ = RX_IDLE;
      }
      break;

    case RX_DATA:
      if (byte == 0x10) {
        this->rx_state_ = RX_DLE_ESCAPE;
      } else {
        this->rx_buffer_.push_back(byte);
      }
      break;

    case RX_DLE_ESCAPE:
      if (byte == 0x03) {
        // DLE ETX — end of frame, process complete packet
        this->rx_state_ = RX_IDLE;
        if (!this->rx_buffer_.empty()) {
          process_rx_packet_(this->rx_buffer_);
        }
        this->rx_buffer_.clear();
      } else if (byte == 0x10) {
        // DLE DLE — escaped literal 0x10
        this->rx_buffer_.push_back(0x10);
        this->rx_state_ = RX_DATA;
      } else if (byte == 0x02) {
        // DLE STX — new frame start (previous was corrupted)
        ESP_LOGW(TAG, "Unexpected DLE STX in data, restarting frame");
        this->rx_buffer_.clear();
        this->rx_state_ = RX_DATA;
      } else {
        // Unknown DLE sequence — treat 0x10 as data
        this->rx_buffer_.push_back(0x10);
        this->rx_buffer_.push_back(byte);
        this->rx_state_ = RX_DATA;
      }
      break;
  }
}

/////////////////////////////////////////////////////////////////////////////////////////////
void JandyPump::process_rx_packet_(const std::vector<uint8_t> &packet) {
  if (packet.size() < 3) {
    ESP_LOGW(TAG, "RX packet too short (%d bytes)", packet.size());
    return;
  }

  // Log received packet
  std::string hex_str;
  for (auto b : packet) {
    char buf[4];
    snprintf(buf, sizeof(buf), "%02X ", b);
    hex_str += buf;
  }
  ESP_LOGD(TAG, "RX: %s", hex_str.c_str());

  // Validate checksum
  uint8_t expected_cs = 0x10 + 0x02;
  for (size_t i = 0; i < packet.size() - 1; i++) {
    expected_cs += packet[i];
  }
  expected_cs &= 0xFF;
  uint8_t actual_cs = packet.back();

  // Accept standard checksum OR checksum+5 (known pump firmware quirk for
  // addr=0x20 responses and Config 0x64 — see PROTOCOL.md "Checksum +5 quirk")
  uint8_t expected_cs_plus5 = (expected_cs + 5) & 0xFF;
  if (expected_cs != actual_cs && expected_cs_plus5 != actual_cs) {
    ESP_LOGW(TAG, "RX checksum mismatch: expected 0x%02X (or +5=0x%02X), got 0x%02X",
             expected_cs, expected_cs_plus5, actual_cs);
    return;
  }

  // Strip leading 0x00 bytes — the pump sends a null preamble byte inside the
  // DLE frame that wasn't present in the original controller's traffic.
  // 0x00 doesn't affect the checksum (already validated above).
  size_t offset = 0;
  while (offset < packet.size() && packet[offset] == 0x00) {
    offset++;
  }
  // Need at least addr + func + checksum after stripping
  if (offset + 3 > packet.size()) {
    ESP_LOGW(TAG, "RX packet too short after stripping null preamble");
    return;
  }

  uint8_t addr = packet[offset];
  uint8_t func = packet[offset + 1];

  // Build data vector: addr + func + data (excluding leading nulls and checksum)
  std::vector<uint8_t> data(packet.begin() + offset, packet.end() - 1);

  ESP_LOGD(TAG, "RX parsed: addr=0x%02X func=0x%02X data_len=%d", addr, func, data.size());

  // Check for NACK response (addr=0xFF)
  if (addr == JANDY_ADDR_NACK) {
    uint8_t nack_code = (data.size() >= 3) ? data[2] : 0;
    ESP_LOGW(TAG, "NACK for func 0x%02X, code=0x%02X", func, nack_code);
    // Remove the pending command — don't retry NACKs
    if (!command_queue_.empty()) {
      auto &current_command = command_queue_.front();
      if (current_command != nullptr && current_command->function_ == func) {
        command_queue_.pop_front();
      }
    }
    this->waiting_for_response_ = false;
    return;
  }

  // Check if this is a response to a pending command
  if (!command_queue_.empty()) {
    auto &current_command = command_queue_.front();
    if (current_command != nullptr && current_command->function_ == func) {
      current_command->payload_ = data;
      this->response_queue_.push(std::move(current_command));
      command_queue_.pop_front();
      this->waiting_for_response_ = false;
      return;
    }
  }

  ESP_LOGV(TAG, "RX: unsolicited packet from 0x%02X func 0x%02X", addr, func);
  this->waiting_for_response_ = false;
}

/////////////////////////////////////////////////////////////////////////////////////////////
bool JandyPump::send_next_command_() {
  uint32_t elapsed = millis() - this->last_command_timestamp_;
  if (elapsed > this->command_throttle_ && !this->waiting_for_response_ && !command_queue_.empty()) {
    auto &command = command_queue_.front();

    if (command->send_countdown < 1) {
      ESP_LOGD(TAG, "Command 0x%02X no response — removed from queue", command->function_);
      command_queue_.pop_front();
    } else {
      ESP_LOGV(TAG, "Sending command 0x%02X (retries left: %d)", command->function_, command->send_countdown);
      command->send();
      this->last_command_timestamp_ = millis();
    }
  }
  return true;
}

/////////////////////////////////////////////////////////////////////////////////////////////
// JandyPumpCommand implementation
/////////////////////////////////////////////////////////////////////////////////////////////

bool JandyPumpCommand::send() {
  std::vector<uint8_t> payload;
  payload.push_back(JANDY_PUMP_ADDR);
  payload.push_back(function_);
  payload.insert(payload.end(), payload_.begin(), payload_.end());
  pump_->send_jandy_raw(payload, cs_offset_);
  this->send_countdown--;
  return true;
}

/////////////////////////////////////////////////////////////////////////////////////////////
JandyPumpCommand JandyPumpCommand::create_status_command(
    JandyPump *pump,
    std::function<void(JandyPump *pump, bool running)> on_status_func) {
  JandyPumpCommand cmd = {};
  cmd.pump_ = pump;
  cmd.function_ = JANDY_FUNC_STATUS;
  cmd.on_data_func_ = [=](JandyPump *pump, const std::vector<uint8_t> data) {
    // Response: [addr=1F] [func=43] [optional status byte]
    // data includes addr+func+data (no checksum)
    if (data.size() <= 2) {
      // No status byte — motor stopped
      ESP_LOGD(TAG, "Status: stopped (no status byte)");
      on_status_func(pump, false);
    } else {
      uint8_t status = data[2];
      ESP_LOGD(TAG, "Status: 0x%02X", status);
      if (status == JANDY_STATUS_RUNNING)
        on_status_func(pump, true);
      else if (status == JANDY_STATUS_BOOT)
        on_status_func(pump, true);  // booting counts as "on"
      else
        on_status_func(pump, false);
    }
  };
  return cmd;
}

/////////////////////////////////////////////////////////////////////////////////////////////
JandyPumpCommand JandyPumpCommand::create_read_sensor_command(
    JandyPump *pump, uint8_t sensor_addr, uint16_t scale,
    std::function<void(JandyPump *pump, uint16_t value)> on_value_func) {
  JandyPumpCommand cmd = {};
  cmd.pump_ = pump;
  cmd.function_ = JANDY_FUNC_READ_SENSOR;
  cmd.payload_.push_back(sensor_addr);
  cmd.on_data_func_ = [=](JandyPump *pump, const std::vector<uint8_t> data) {
    // Response: [addr=1F] [func=45] [sensor_addr] [val_lo] [val_hi]
    if (data.size() >= 5) {
      uint16_t raw = (uint16_t)data[3] | ((uint16_t)data[4] << 8);
      float value = (float)raw / (float)scale;
      ESP_LOGD(TAG, "Sensor 0x%02X = %.3f (raw=%d, scale /%d)", sensor_addr, value, raw, scale);
      on_value_func(pump, raw);
    } else if (data.size() >= 3) {
      // Short response — possibly no value available
      ESP_LOGD(TAG, "Sensor 0x%02X: short response (%d bytes)", sensor_addr, data.size());
    }
  };
  return cmd;
}

/////////////////////////////////////////////////////////////////////////////////////////////
JandyPumpCommand JandyPumpCommand::create_run_command(
    JandyPump *pump,
    std::function<void(JandyPump *pump)> on_confirmation_func) {
  JandyPumpCommand cmd = {};
  cmd.pump_ = pump;
  cmd.function_ = JANDY_FUNC_GO;
  cmd.on_data_func_ = [=](JandyPump *pump, const std::vector<uint8_t> data) {
    ESP_LOGD(TAG, "Go command confirmed");
    on_confirmation_func(pump);
  };
  return cmd;
}

/////////////////////////////////////////////////////////////////////////////////////////////
JandyPumpCommand JandyPumpCommand::create_stop_command(
    JandyPump *pump,
    std::function<void(JandyPump *pump)> on_confirmation_func) {
  JandyPumpCommand cmd = {};
  cmd.pump_ = pump;
  cmd.function_ = JANDY_FUNC_STOP;
  cmd.on_data_func_ = [=](JandyPump *pump, const std::vector<uint8_t> data) {
    ESP_LOGD(TAG, "Stop command confirmed");
    on_confirmation_func(pump);
  };
  return cmd;
}

/////////////////////////////////////////////////////////////////////////////////////////////
JandyPumpCommand JandyPumpCommand::create_set_demand_command(
    JandyPump *pump, uint16_t rpm,
    std::function<void(JandyPump *pump)> on_confirmation_func) {
  JandyPumpCommand cmd = {};
  cmd.pump_ = pump;
  cmd.function_ = JANDY_FUNC_SET_DEMAND;
  // Jandy demand = RPM * 4, little-endian
  uint16_t demand = rpm * 4;
  cmd.payload_.push_back(demand & 0xFF);
  cmd.payload_.push_back((demand >> 8) & 0xFF);
  cmd.on_data_func_ = [=](JandyPump *pump, const std::vector<uint8_t> data) {
    ESP_LOGD(TAG, "Set demand %d RPM confirmed", rpm);
    on_confirmation_func(pump);
  };
  return cmd;
}

/////////////////////////////////////////////////////////////////////////////////////////////
// Initialization sequence — mimics the original Jandy controller startup.
// The pump requires ReadID and Config handshakes before accepting Set Demand
// or Read Sensor commands. Without this, those commands receive NACK 0x03.
void JandyPump::send_fire_and_forget_(uint8_t func, const std::vector<uint8_t> &payload, uint8_t cs_offset) {
  std::vector<uint8_t> raw;
  raw.push_back(JANDY_PUMP_ADDR);
  raw.push_back(func);
  raw.insert(raw.end(), payload.begin(), payload.end());
  send_jandy_raw(raw, cs_offset);
  // Don't wait for response — just return to RX mode
  this->waiting_for_response_ = false;
}

}  // namespace jandy_pump
}  // namespace esphome
