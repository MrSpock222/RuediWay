// RuediWay camera client for Seeed XIAO ESP32-S3 Sense.
// Arduino ESP32 3.3.12; OPI PSRAM + USB CDC enabled. No extra libraries.
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <esp_camera.h>
#include <cJSON.h>
#include <atomic>

Preferences prefs;
SemaphoreHandle_t configLock;
String hostUrl, cameraToken, wifiSsid, wifiPassword;
String pendingCode, serialLine, liveSession;
std::atomic<bool> authExpired{false};
bool cameraReady = false, connecting = false, serialOverflow = false;
uint32_t connectStarted = 0, nextPoll = 0, nextFrame = 0, nextReconnect = 0;
int liveRevision = 0, activeSize = -1, activeQuality = -1, activeExposure = 99;
bool scanProfile = false;
int desiredExposure = -1;
const size_t MAX_FRAME = 2 * 1024 * 1024;
std::atomic<uint32_t> lastCaptureMs{0};
uint32_t lastUploadMs = 0, lastPollMs = 0;
uint32_t frameBytes = 0, framesSent = 0, frameErrors = 0;
int lastUploadCode = 0;

// A connection belongs to exactly one task; the heartbeat must never share
// HTTP state with an in-flight image upload. Disable Nagle after each connect.
class CameraSocket : public WiFiClient {
public:
  int connect(IPAddress ip, uint16_t port, int32_t timeout) override {
    int result = WiFiClient::connect(ip, port, timeout);
    if (result) setNoDelay(true);
    return result;
  }
};
struct Transport {
  CameraSocket socket;
  HTTPClient http;
  String server;
};
Transport cameraTransport, heartbeatTransport;

String field(cJSON *object, const char *key) {
  cJSON *value = cJSON_GetObjectItemCaseSensitive(object, key);
  return cJSON_IsString(value) ? String(value->valuestring) : String();
}
int number(cJSON *object, const char *key, int fallback = 0) {
  cJSON *value = cJSON_GetObjectItemCaseSensitive(object, key);
  return cJSON_IsNumber(value) ? (int)round(value->valuedouble) : fallback;
}
void event(const char *name, const char *message) {
  cJSON *doc = cJSON_CreateObject();
  cJSON_AddStringToObject(doc, "event", name);
  cJSON_AddStringToObject(doc, "message", message);
  char *json = cJSON_PrintUnformatted(doc);
  if (json) { Serial.println(json); free(json); }
  cJSON_Delete(doc);
}
void copyConfig(String &server, String &token) {
  xSemaphoreTake(configLock, portMAX_DELAY);
  server = hostUrl; token = cameraToken;
  xSemaphoreGive(configLock);
}
void setToken(const String &token) {
  xSemaphoreTake(configLock, portMAX_DELAY);
  cameraToken = token;
  xSemaphoreGive(configLock);
  prefs.putString("token", token);
}
bool validHost(const String &url) {
  if (!url.startsWith("http://") || url.length() > 160) return false;
  String authority = url.substring(7);
  if (authority.length() == 0) return false;
  for (size_t i = 0; i < authority.length(); ++i) {
    char c = authority[i];
    if (!(isalnum((unsigned char)c) || c == '.' || c == '-' || c == ':')) return false;
  }
  return true;
}
bool validId(const String &value) {
  if (value.length() < 8 || value.length() > 100) return false;
  for (char c : value) if (!(isalnum((unsigned char)c) || c == '_' || c == '-')) return false;
  return true;
}

int request(const String &path, const char *method, const uint8_t *body, size_t length,
            const char *type, String *response = nullptr, bool authenticated = true,
            bool heartbeatRequest = false, bool previewOnly = false) {
  String server, token; copyConfig(server, token);
  if (WiFi.status() != WL_CONNECTED || !validHost(server)) return -1;
  if (authenticated && token.isEmpty()) return -1;
  Transport &transport = heartbeatRequest ? heartbeatTransport : cameraTransport;
  HTTPClient &http = transport.http;
  // Also close when only the port changes; HTTPClient checks hostname only.
  if (transport.server != server) {
    transport.socket.stop();
    transport.server = server;
  }
  http.setConnectTimeout(3000);
  http.setTimeout(8000);
  http.setReuse(true);
  if (!http.begin(transport.socket, server + path)) {
    transport.socket.stop();
    return -1;
  }
  if (authenticated) http.addHeader("X-Ruediway-Token", token);
  if (type) http.addHeader("Content-Type", type);
  if (previewOnly) http.addHeader("X-Ruediway-Preview-Only", "1");
  int code = http.sendRequest(method, const_cast<uint8_t *>(body), length);
  // Drain even acknowledgements before reusing the connection. An incomplete
  // or unexpected response must not become the next request's HTTP headers.
  if (code > 0 && http.getSize() >= 0 && http.getSize() <= 8192) {
    int expected = http.getSize();
    String received = http.getString();
    if ((int)received.length() == expected) {
      if (response) *response = received;
    } else {
      transport.socket.stop();
      code = HTTPC_ERROR_READ_TIMEOUT;
    }
  } else transport.socket.stop();
  http.end();
  if (authenticated && (code == 401 || code == 403)) {
    xSemaphoreTake(configLock, portMAX_DELAY);
    if (token == cameraToken) authExpired = true;
    xSemaphoreGive(configLock);
  }
  return code;
}

#include "capture_pipeline.h"

int jsonRequest(const String &path, cJSON *body, String *response = nullptr, bool auth = true) {
  char *json = cJSON_PrintUnformatted(body);
  if (!json) return -1;
  int code = request(path, "POST", (uint8_t *)json, strlen(json), "application/json", response, auth);
  free(json);
  return code;
}

void status() {
  cJSON *doc = cJSON_CreateObject();
  cJSON_AddStringToObject(doc, "event", "status");
  cJSON_AddStringToObject(doc, "firmware", "ruediway-xiao-3");
  cJSON_AddBoolToObject(doc, "camera_ready", cameraReady);
  sensor_t *sensor = cameraReady ? esp_camera_sensor_get() : nullptr;
  cJSON_AddNumberToObject(doc, "sensor_pid", sensor ? sensor->id.PID : 0);
  cJSON_AddNumberToObject(doc, "psram_bytes", ESP.getPsramSize());
  cJSON_AddNumberToObject(doc, "wifi_rssi_dbm", WiFi.RSSI());
  cJSON_AddNumberToObject(doc, "free_heap", ESP.getFreeHeap());
  cJSON_AddNumberToObject(doc, "capture_ms", lastCaptureMs.load());
  cJSON_AddBoolToObject(doc, "pipeline_ready", pipelineReady);
  cJSON_AddNumberToObject(doc, "free_psram", ESP.getFreePsram());
  cJSON_AddNumberToObject(doc, "frames_captured", capturedFrames.load());
  cJSON_AddNumberToObject(doc, "frames_replaced", replacedFrames.load());
  cJSON_AddNumberToObject(doc, "preview_frames_sent", previewFrames.load());
  cJSON_AddNumberToObject(doc, "decode_failures", decodeFailures.load());
  cJSON_AddNumberToObject(doc, "capture_failures", oversizeFrames.load());
  cJSON_AddNumberToObject(doc, "selection_ms", lastSelectMs.load());
  cJSON_AddNumberToObject(doc, "decode_ms", lastDecodeMs.load());
  cJSON_AddNumberToObject(doc, "measure_ms", lastMeasureMs.load());
  cJSON_AddNumberToObject(doc, "encode_ms", lastEncodeMs.load());
  cJSON_AddNumberToObject(doc, "frame_age_ms", lastFrameAgeMs.load());
  cJSON_AddNumberToObject(doc, "coarse_sharpness", measuredSharpness.load());
  cJSON_AddNumberToObject(doc, "coarse_movement", measuredMovement.load());
  cJSON_AddNumberToObject(doc, "upload_ms", lastUploadMs);
  cJSON_AddNumberToObject(doc, "poll_ms", lastPollMs);
  cJSON_AddNumberToObject(doc, "frame_bytes", frameBytes);
  cJSON_AddNumberToObject(doc, "frames_sent", framesSent);
  cJSON_AddNumberToObject(doc, "frame_errors", frameErrors);
  cJSON_AddNumberToObject(doc, "upload_code", lastUploadCode);
  cJSON_AddBoolToObject(doc, "wifi_connected", WiFi.status() == WL_CONNECTED);
  cJSON_AddStringToObject(doc, "ssid", wifiSsid.c_str());
  cJSON_AddStringToObject(doc, "ip", WiFi.localIP().toString().c_str());
  cJSON_AddStringToObject(doc, "server", hostUrl.c_str());
  cJSON_AddBoolToObject(doc, "paired", !cameraToken.isEmpty() && !authExpired);
  char *json = cJSON_PrintUnformatted(doc);
  if (json) { Serial.println(json); free(json); }
  cJSON_Delete(doc);
}

bool initCamera() {
  if (!psramFound()) { event("error", "OPI PSRAM fehlt. Board-Einstellung pruefen."); return false; }
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0; c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = 15; c.pin_d1 = 17; c.pin_d2 = 18; c.pin_d3 = 16;
  c.pin_d4 = 14; c.pin_d5 = 12; c.pin_d6 = 11; c.pin_d7 = 48;
  c.pin_xclk = 10; c.pin_pclk = 13; c.pin_vsync = 38; c.pin_href = 47;
  c.pin_sccb_sda = 40; c.pin_sccb_scl = 39;
  c.pin_pwdn = -1; c.pin_reset = -1; c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG; c.frame_size = FRAMESIZE_UXGA;
  c.jpeg_quality = 10; c.fb_count = 2; c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) { event("error", "Kamera nicht erkannt. Sense-Kameraplatine und Kabel pruefen."); return false; }
  sensor_t *s = esp_camera_sensor_get();
  s->set_whitebal(s, 1); s->set_gain_ctrl(s, 1); s->set_exposure_ctrl(s, 1);
  if (s->id.PID == OV3660_PID) { s->set_vflip(s, 1); s->set_saturation(s, -1); }
  activeSize = FRAMESIZE_UXGA; activeQuality = 10;
  event("camera_ready", "Kamera und PSRAM bereit.");
  return true;
}

bool profile(framesize_t size, int quality, int exposure) {
  if (!cameraReady) return false;
  exposure = constrain(exposure, -2, 2);
  if (activeSize == size && activeQuality == quality && activeExposure == exposure) return true;
  sensor_t *s = esp_camera_sensor_get();
  if (s->set_framesize(s, size) != 0 || s->set_quality(s, quality) != 0) return false;
  s->set_exposure_ctrl(s, 1); s->set_ae_level(s, exposure);
  activeSize = size; activeQuality = quality; activeExposure = exposure;
  // Discard queued images after resolution/exposure changes.
  delay(200);
  for (int i = 0; i < 3; ++i) { camera_fb_t *fb = esp_camera_fb_get(); if (fb) esp_camera_fb_return(fb); }
  return true;
}

void reportError(const String &path, const char *message) {
  cJSON *doc = cJSON_CreateObject();
  cJSON_AddStringToObject(doc, "message", message);
  jsonRequest(path, doc);
  cJSON_Delete(doc);
}
void captureJob(cJSON *job) {
  String id = field(job, "id");
  if (!validId(id)) return;
  configureStream("");
  SemaphoreGuard cameraGuard(cameraLock);
  int exposure = number(cJSON_GetObjectItem(job, "settings"), "ev", -1);
  if (!profile(FRAMESIZE_UXGA, 10, exposure)) {
    reportError("/camera/jobs/" + id + "/error", "ESP32-Kamera nicht bereit."); return;
  }
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb || fb->format != PIXFORMAT_JPEG || !fb->len || fb->len > MAX_FRAME) {
    if (fb) esp_camera_fb_return(fb);
    reportError("/camera/jobs/" + id + "/error", "ESP32 konnte kein brauchbares JPEG aufnehmen."); return;
  }
  // Host acknowledges the upload immediately and runs analysis in background.
  int code = request("/camera/jobs/" + id + "/jpeg", "POST", fb->buf, fb->len, "image/jpeg");
  esp_camera_fb_return(fb);
  if (code == 202) event("photo_uploaded", "Foto am Host angekommen.");
  else if (code > 0 && code != 401 && code != 403 && code != 409)
    reportError("/camera/jobs/" + id + "/error", "JPEG-Upload abgelehnt. Host-Version pruefen.");
  else event("upload_uncertain", "Upload nicht bestaetigt. Status am Host pruefen; keine doppelte Aufnahme.");
  nextPoll = millis() + 200;
}

void heartbeat(void *) {
  for (;;) {
    request("/camera/heartbeat", "POST", nullptr, 0, nullptr, nullptr, true, true);
    vTaskDelay(pdMS_TO_TICKS(4000));
  }
}
void connectWifi() {
  WiFi.disconnect();
  WiFi.begin(wifiSsid.c_str(), wifiPassword.c_str());
  connecting = true; connectStarted = millis();
  event("connecting", "Verbinde mit dem 2,4-GHz-WLAN ...");
}
void pairNow() {
  if (!cameraReady) { pendingCode = ""; event("error", "Kamera nicht bereit; Kopplung abgebrochen."); return; }
  cJSON *doc = cJSON_CreateObject();
  cJSON_AddStringToObject(doc, "code", pendingCode.c_str());
  cJSON_AddStringToObject(doc, "device", "camera");
  cJSON_AddStringToObject(doc, "camera_model", "xiao_esp32s3");
  String response;
  int code = jsonRequest("/pair", doc, &response, false);
  cJSON_Delete(doc); pendingCode = "";
  doc = cJSON_Parse(response.c_str());
  String token = field(doc, "token"); cJSON_Delete(doc);
  if (code == 200 && token.length() >= 20 && token.length() <= 128) {
    authExpired = false; setToken(token); nextPoll = 0;
    event("paired", "ESP32-Kamera gekoppelt. Bereit fuer Foto, Live und Scan.");
  } else event("error", "Kopplung fehlgeschlagen. PC-Adresse, Verbindung und neuen Host-Code pruefen.");
}
void command(const String &line) {
  cJSON *doc = cJSON_Parse(line.c_str());
  if (!doc) { event("error", "Ungueltiger USB-Befehl."); return; }
  String cmd = field(doc, "cmd");
  if (cmd == "status") status();
  else if (cmd == "configure" || cmd == "pair") {
    String server = field(doc, "server"), code = field(doc, "code");
    while (server.endsWith("/")) server.remove(server.length() - 1);
    bool goodCode = code.length() == 6;
    for (char c : code) if (c < '0' || c > '9') goodCode = false;
    String ssid = field(doc, "ssid"), password = field(doc, "password");
    bool validWifi = cmd == "pair" || (ssid.length() > 0 && ssid.length() <= 32 &&
                      (password.length() == 0 || (password.length() >= 8 && password.length() <= 63)));
    if (!validHost(server) || !goodCode || !validWifi) event("error", "PC-Adresse, WLAN-Daten oder sechsstelligen Code pruefen.");
    else {
      configureStream(""); setToken(""); authExpired = false; liveSession = "";
      xSemaphoreTake(configLock, portMAX_DELAY); hostUrl = server; xSemaphoreGive(configLock);
      prefs.putString("server", server); pendingCode = code;
      if (cmd == "configure") {
        wifiSsid = ssid; wifiPassword = password;
        prefs.putString("ssid", ssid); prefs.putString("password", password);
      }
      if (wifiSsid.isEmpty()) { pendingCode = ""; event("error", "WLAN zuerst einrichten."); }
      else if (cmd == "configure" || WiFi.status() != WL_CONNECTED) connectWifi();
    }
  } else event("error", "Unbekannter USB-Befehl.");
  cJSON_Delete(doc);
}

void setup() {
  Serial.begin(115200);
  configLock = xSemaphoreCreateMutex();
  prefs.begin("ruediway", false);
  hostUrl = prefs.getString("server", ""); cameraToken = prefs.getString("token", "");
  wifiSsid = prefs.getString("ssid", ""); wifiPassword = prefs.getString("password", "");
  cameraReady = initCamera();
  if (!initPipeline()) {
    cameraReady = false;
    event("error", "Kamerapuffer konnte nicht gestartet werden. PSRAM pruefen.");
  }
  WiFi.mode(WIFI_STA); WiFi.setSleep(false); WiFi.setAutoReconnect(true);
  if (!wifiSsid.isEmpty()) connectWifi();
  xTaskCreate(heartbeat, "camera-heartbeat", 6144, nullptr, 1, nullptr);
  event("ready", "RuediWay bereit. USB-Einrichtung starten.");
}
void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      if (!serialOverflow && serialLine.length()) command(serialLine);
      if (serialOverflow) event("error", "USB-Befehl zu lang.");
      serialLine = ""; serialOverflow = false;
    } else if (c != '\r') {
      if (serialLine.length() < 2048 && !serialOverflow) serialLine += c;
      else { serialLine = ""; serialOverflow = true; }
    }
  }
  if (authExpired) {
    configureStream(""); authExpired = false; setToken(""); liveSession = "";
    event("pairing_required", "Kopplung abgelaufen. Per USB mit neuem Host-Code koppeln.");
  }
  if (connecting) {
    if (WiFi.status() == WL_CONNECTED) { connecting = false; event("wifi_connected", "WLAN verbunden."); }
    else if (millis() - connectStarted > 35000) { connecting = false; pendingCode = ""; nextReconnect = millis() + 10000; event("error", "WLAN nicht erreichbar. Passwort, 2,4 GHz und Antenne pruefen."); }
  }
  if (WiFi.status() != WL_CONNECTED) {
    configureStream(""); liveSession = "";
    if (!connecting && !wifiSsid.isEmpty() && (int32_t)(millis() - nextReconnect) >= 0) { nextReconnect = millis() + 15000; WiFi.reconnect(); }
    delay(10); return;
  }
  if (!pendingCode.isEmpty()) pairNow();
  if (cameraToken.isEmpty() || !cameraReady) { delay(20); return; }
  if ((int32_t)(millis() - nextPoll) >= 0) {
    String response;
    uint32_t pollStarted = millis();
    int code = request("/camera/next", "GET", nullptr, 0, nullptr, &response);
    lastPollMs = millis() - pollStarted;
    nextPoll = millis() + (code == 200 ? 800 : 3000);
    if (code != 200) { configureStream(""); liveSession = ""; delay(10); return; }
    cJSON *doc = cJSON_Parse(response.c_str());
    if (!doc) { configureStream(""); liveSession = ""; delay(10); return; }
    cJSON *live = cJSON_GetObjectItem(doc, "live");
    liveSession = field(live, "session");
    if (!validId(liveSession)) liveSession = "";
    liveRevision = number(live, "revision"); scanProfile = field(live, "profile") == "scan";
    desiredExposure = number(cJSON_GetObjectItem(live, "settings"), "ev", -1);
    cJSON *job = cJSON_GetObjectItem(doc, "job");
    if (cJSON_IsObject(job)) { captureJob(job); cJSON_Delete(doc); delay(10); return; }
    configureStream(liveSession, liveRevision, scanProfile, desiredExposure);
    cJSON_Delete(doc);
  }
  if (!liveSession.isEmpty() && (int32_t)(millis() - nextFrame) >= 0) {
    FrameSlot *frame = takeFrame();
    if (frame) {
      nextFrame = millis() + (scanProfile ? 200 : 100);
      frameBytes = frame->length;
      lastFrameAgeMs = millis() - frame->captured;
      uint32_t uploadStarted = millis();
      int code = request("/camera/live/" + String(frame->session) + "/" + String(frame->revision) + "/frame",
                         "POST", frame->data, frame->length, "image/jpeg", nullptr, true, false, frame->previewOnly);
      lastUploadMs = millis() - uploadStarted; lastUploadCode = code;
      if (code == 200) { ++framesSent; if (frame->previewOnly) ++previewFrames; }
      else if (code != 409) ++frameErrors; // normal session stop is not a transfer failure
      releaseFrame(frame, code == 200);
      if (code != 200) { configureStream(""); liveSession = ""; nextPoll = millis() + 500; }
    }
  }
  delay(5);
}
