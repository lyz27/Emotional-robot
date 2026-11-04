#include <Arduino.h>
#include <ArduinoJson.h>

namespace {

constexpr uint32_t kBaudRate = 115200;
constexpr uint32_t kHeartbeatTimeoutMs = 2000;
constexpr uint32_t kStatusIntervalMs = 1000;
constexpr size_t kMaxInputBytes = 1024;
constexpr uint8_t kProtocolVersion = 1;
constexpr char kFirmwareVersion[] = "0.1.0";

String inputLine;
uint32_t lastHeartbeatMs = 0;
uint32_t lastStatusMs = 0;
bool linkEstablished = false;
bool armed = false;
bool estopLatched = false;
bool heartbeatFault = false;

const char* stateName() {
  if (estopLatched) return "estop";
  if (heartbeatFault) return "fault";
  if (armed) return "armed";
  return "safe";
}

void safeStop() {
  // Future actuator outputs must be disabled here before adding any motion driver.
  armed = false;
}

void addCommon(JsonDocument& response, uint32_t id, const char* type) {
  response["v"] = kProtocolVersion;
  response["id"] = id;
  response["type"] = type;
  response["device_ms"] = millis();
}

void sendDocument(JsonDocument& response) {
  serializeJson(response, Serial);
  Serial.write('\n');
}

void sendError(uint32_t id, const char* requestType, const char* code) {
  JsonDocument response;
  addCommon(response, id, "error");
  response["request"] = requestType;
  response["ok"] = false;
  response["error"] = code;
  response["state"] = stateName();
  sendDocument(response);
}

void addStatus(JsonDocument& response) {
  response["state"] = stateName();
  response["armed"] = armed;
  response["estop"] = estopLatched;
  response["heartbeat_fault"] = heartbeatFault;
  response["link"] = linkEstablished;
  response["free_heap"] = ESP.getFreeHeap();
}

void sendStatusEvent() {
  JsonDocument response;
  addCommon(response, 0, "status");
  addStatus(response);
  sendDocument(response);
}

void handleMessage(const String& line) {
  JsonDocument request;
  const DeserializationError parseError = deserializeJson(request, line);
  if (parseError) {
    sendError(0, "unknown", "invalid_json");
    return;
  }

  const uint32_t id = request["id"] | 0;
  const uint8_t version = request["v"] | 0;
  const char* type = request["type"] | "";
  if (version != kProtocolVersion) {
    sendError(id, type, "unsupported_version");
    return;
  }
  if (type[0] == '\0') {
    sendError(id, "unknown", "missing_type");
    return;
  }

  JsonDocument response;
  addCommon(response, id, type);
  response["ok"] = true;

  if (strcmp(type, "hello") == 0) {
    linkEstablished = true;
    lastHeartbeatMs = millis();
    heartbeatFault = false;
    response["firmware"] = kFirmwareVersion;
    response["protocol"] = kProtocolVersion;
    response["chip"] = ESP.getChipModel();
    addStatus(response);
  } else if (strcmp(type, "heartbeat") == 0) {
    linkEstablished = true;
    lastHeartbeatMs = millis();
    heartbeatFault = false;
    response["state"] = stateName();
  } else if (strcmp(type, "get_status") == 0) {
    addStatus(response);
    response["firmware"] = kFirmwareVersion;
  } else if (strcmp(type, "arm") == 0) {
    const bool heartbeatFresh = linkEstablished &&
        static_cast<uint32_t>(millis() - lastHeartbeatMs) <= kHeartbeatTimeoutMs;
    if (!heartbeatFresh || heartbeatFault || estopLatched) {
      sendError(id, type, "unsafe_to_arm");
      return;
    }
    armed = true;
    response["state"] = stateName();
  } else if (strcmp(type, "disarm") == 0) {
    safeStop();
    response["state"] = stateName();
  } else if (strcmp(type, "estop") == 0) {
    estopLatched = true;
    safeStop();
    response["state"] = stateName();
  } else if (strcmp(type, "clear_faults") == 0) {
    safeStop();
    estopLatched = false;
    heartbeatFault = false;
    response["state"] = stateName();
  } else if (strcmp(type, "ping") == 0) {
    response["pong"] = true;
  } else {
    sendError(id, type, "unknown_command");
    return;
  }

  sendDocument(response);
}

void pollSerial() {
  while (Serial.available() > 0) {
    const char value = static_cast<char>(Serial.read());
    if (value == '\n') {
      inputLine.trim();
      if (!inputLine.isEmpty()) handleMessage(inputLine);
      inputLine = "";
      continue;
    }
    if (value == '\r') continue;
    if (inputLine.length() >= kMaxInputBytes) {
      inputLine = "";
      sendError(0, "unknown", "frame_too_large");
      continue;
    }
    inputLine += value;
  }
}

void enforceSafety() {
  if (!linkEstablished) return;
  if (static_cast<uint32_t>(millis() - lastHeartbeatMs) > kHeartbeatTimeoutMs) {
    if (!heartbeatFault) {
      heartbeatFault = true;
      safeStop();
      sendStatusEvent();
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(kBaudRate);
  inputLine.reserve(kMaxInputBytes);
  delay(100);
  sendStatusEvent();
}

void loop() {
  pollSerial();
  enforceSafety();
  if (static_cast<uint32_t>(millis() - lastStatusMs) >= kStatusIntervalMs) {
    lastStatusMs = millis();
    sendStatusEvent();
  }
  delay(1);
}

