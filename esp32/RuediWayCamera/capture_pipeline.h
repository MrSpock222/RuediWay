#pragma once
#include <img_converters.h>
#include <esp_heap_caps.h>
#include "frame_quality.h"

// One frame uploading, one pending, one being captured. No growing queue.
constexpr size_t STREAM_FRAME_LIMIT = 768 * 1024;
constexpr size_t THUMB_BYTES = 160 * 128 * 3;
constexpr size_t JPEG_WORK_BYTES = 68 * 1024;
enum SlotState { SLOT_FREE, SLOT_WRITING, SLOT_PENDING, SLOT_UPLOADING };
struct StreamConfig {
  char session[101] = {};
  uint32_t generation = 0, refreshed = 0;
  int revision = 0, exposure = -1;
  bool scan = false, enabled = false;
};
struct FrameSlot {
  uint8_t *data = nullptr;
  size_t length = 0;
  uint32_t captured = 0, generation = 0;
  int revision = 0;
  char session[101] = {};
  SlotState state = SLOT_FREE;
  bool previewOnly = false;
  FrameQuality quality;
};
SemaphoreHandle_t streamLock, cameraLock;
FrameSlot frameSlots[3];
StreamConfig streamConfig;
FrameQuality sentReference;
uint8_t *thumbnail = nullptr;
void *jpegWorkspace = nullptr;
bool pipelineReady = false;
uint32_t lastNativeSent = 0;
std::atomic<uint32_t> capturedFrames{0}, replacedFrames{0}, previewFrames{0}, decodeFailures{0};
std::atomic<uint32_t> oversizeFrames{0}, lastSelectMs{0}, lastFrameAgeMs{0};
std::atomic<uint32_t> lastDecodeMs{0}, lastMeasureMs{0}, lastEncodeMs{0};
std::atomic<float> measuredSharpness{0}, measuredMovement{0};

class SemaphoreGuard {
  SemaphoreHandle_t handle;
public:
  explicit SemaphoreGuard(SemaphoreHandle_t h) : handle(h) { xSemaphoreTake(handle, portMAX_DELAY); }
  ~SemaphoreGuard() { xSemaphoreGive(handle); }
};
bool profile(framesize_t size, int quality, int exposure);

void configureStream(const String &session, int revision = 0, bool scan = false, int exposure = -1) {
  if (!streamLock) return;
  SemaphoreGuard guard(streamLock);
  bool enabled = !session.isEmpty() && pipelineReady;
  if (String(streamConfig.session) != session || streamConfig.revision != revision ||
      streamConfig.scan != scan || streamConfig.exposure != exposure || streamConfig.enabled != enabled) {
    ++streamConfig.generation;
    for (auto &slot : frameSlots) if (slot.state == SLOT_PENDING) slot.state = SLOT_FREE;
    sentReference = FrameQuality(); lastNativeSent = 0;
  }
  strlcpy(streamConfig.session, session.c_str(), sizeof(streamConfig.session));
  streamConfig.revision = revision; streamConfig.scan = scan;
  streamConfig.exposure = exposure; streamConfig.enabled = enabled;
  streamConfig.refreshed = millis();
}

void captureStream(void *) {
  uint32_t nextCapture = 0;
  for (;;) {
    StreamConfig config;
    FrameQuality reference;
    uint32_t nativeSent;
    int index = -1;
    {
      SemaphoreGuard guard(streamLock);
      config = streamConfig; reference = sentReference; nativeSent = lastNativeSent;
      if (config.enabled && millis() - config.refreshed < 10000 &&
          !authExpired.load() && WiFi.status() == WL_CONNECTED && (int32_t)(millis()-nextCapture) >= 0) {
        for (int i = 0; i < 3; ++i) if (frameSlots[i].state == SLOT_FREE) {
          frameSlots[i].state = SLOT_WRITING; index = i; break;
        }
      }
    }
    if (index < 0) { vTaskDelay(pdMS_TO_TICKS(10)); continue; }
    nextCapture = millis() + (config.scan ? 120 : 100);
    FrameSlot &slot = frameSlots[index];
    slot.length = 0; slot.previewOnly = false; slot.quality = FrameQuality();
    slot.generation = config.generation; slot.revision = config.revision;
    strlcpy(slot.session, config.session, sizeof(slot.session));
    {
      SemaphoreGuard cameraGuard(cameraLock);
      if (profile(config.scan ? FRAMESIZE_SXGA : FRAMESIZE_VGA, config.scan ? 12 : 20, config.exposure)) {
        uint32_t before = millis();
        camera_fb_t *fb = esp_camera_fb_get();
        lastCaptureMs = millis() - before;
        slot.captured = millis();
        if (fb && fb->format == PIXFORMAT_JPEG && fb->len && fb->len <= STREAM_FRAME_LIMIT) {
          memcpy(slot.data, fb->buf, fb->len); slot.length = fb->len;
          ++capturedFrames;
        } else ++oversizeFrames;
        if (fb) esp_camera_fb_return(fb);
      }
    }
    // Decode outside the camera lock, so a requested photo can take priority.
    if (slot.length && config.scan) {
      uint32_t before = millis();
      esp_jpeg_image_cfg_t decoder = {};
      decoder.indata = slot.data; decoder.indata_size = slot.length;
      decoder.outbuf = thumbnail; decoder.outbuf_size = THUMB_BYTES;
      decoder.out_scale = JPEG_IMAGE_SCALE_1_8;
      decoder.out_format = JPEG_IMAGE_FORMAT_RGB888;
      decoder.advanced.working_buffer = jpegWorkspace;
      decoder.advanced.working_buffer_size = JPEG_WORK_BYTES;
      esp_jpeg_image_output_t output = {};
      esp_err_t decoded = esp_jpeg_decode(&decoder, &output);
      lastDecodeMs = millis() - before;
      lastEncodeMs = 0;
      if (decoded == ESP_OK && output.width <= 160 && output.height <= 128) {
        uint32_t measuring = millis();
        slot.quality = measureFrame(thumbnail, output.width, output.height, &reference);
        lastMeasureMs = millis() - measuring;
        measuredSharpness = slot.quality.sharpness; measuredMovement = slot.quality.movement;
        // Send an original at least once a second, even when the heuristic is
        // uncertain. A thumbnail is never submitted as a native scan image.
        if (nativeSent && millis() - nativeSent < 1000 && (slot.quality.poor || slot.quality.duplicate)) {
          uint8_t *preview = nullptr; size_t length = 0;
          uint32_t encoding = millis();
          if (fmt2jpg(thumbnail, output.output_len, output.width, output.height, PIXFORMAT_RGB888,
                      75, &preview, &length) && length && length <= STREAM_FRAME_LIMIT) {
            memcpy(slot.data, preview, length); slot.length = length; slot.previewOnly = true;
          }
          free(preview);
          lastEncodeMs = millis() - encoding;
        }
      } else ++decodeFailures; // fail open: host evaluates the original
      lastSelectMs = millis() - before;
    }
    {
      SemaphoreGuard guard(streamLock);
      if (!slot.length || !streamConfig.enabled || config.generation != streamConfig.generation) {
        slot.state = SLOT_FREE;
      } else {
        bool keepPending = false;
        for (auto &other : frameSlots) if (other.state == SLOT_PENDING) {
          // Briefly preserve a good original over a redundant/poor preview.
          // The age bound prevents an old sharp view from blocking movement.
          keepPending = config.scan && !other.previewOnly && millis() - other.captured < 350 &&
                        (slot.previewOnly || (slot.quality.movement < 6 && other.quality.decoded &&
                         slot.quality.decoded && other.quality.sharpness > slot.quality.sharpness * 1.2));
          if (!keepPending) other.state = SLOT_FREE;
          ++replacedFrames;
        }
        slot.state = keepPending ? SLOT_FREE : SLOT_PENDING;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

bool initPipeline() {
  streamLock = xSemaphoreCreateMutex(); cameraLock = xSemaphoreCreateMutex();
  if (!streamLock || !cameraLock) return false;
  // The decoder's many tiny scratch accesses are expensive in external RAM.
  thumbnail = (uint8_t *)heap_caps_malloc(THUMB_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  jpegWorkspace = heap_caps_malloc(JPEG_WORK_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  bool allocated = thumbnail != nullptr && jpegWorkspace != nullptr;
  for (auto &slot : frameSlots) {
    slot.data = (uint8_t *)ps_malloc(STREAM_FRAME_LIMIT);
    allocated = allocated && slot.data;
  }
  if (!allocated) {
    free(thumbnail); thumbnail = nullptr;
    free(jpegWorkspace); jpegWorkspace = nullptr;
    for (auto &slot : frameSlots) { free(slot.data); slot.data = nullptr; }
    return false;
  }
  pipelineReady = true;
  if (xTaskCreatePinnedToCore(captureStream, "camera-capture", 8192, nullptr, 1, nullptr, 1) != pdPASS) {
    pipelineReady = false;
    free(thumbnail); thumbnail = nullptr;
    free(jpegWorkspace); jpegWorkspace = nullptr;
    for (auto &slot : frameSlots) { free(slot.data); slot.data = nullptr; }
    return false;
  }
  return true;
}

FrameSlot *takeFrame() {
  if (!streamLock) return nullptr;
  SemaphoreGuard guard(streamLock);
  for (auto &slot : frameSlots) if (slot.state == SLOT_PENDING) {
    if (millis() - slot.captured > 1500 || slot.generation != streamConfig.generation || !streamConfig.enabled) {
      slot.state = SLOT_FREE; ++replacedFrames; continue;
    }
    slot.state = SLOT_UPLOADING;
    return &slot;
  }
  return nullptr;
}
void releaseFrame(FrameSlot *slot, bool accepted) {
  SemaphoreGuard guard(streamLock);
  if (accepted && !slot->previewOnly && slot->generation == streamConfig.generation && streamConfig.scan) {
    sentReference = slot->quality; lastNativeSent = millis();
  }
  slot->state = SLOT_FREE;
}
