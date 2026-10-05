#pragma once
#include <math.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

// Deliberately coarse: these measurements never certify readable text.
// Original images still receive native-resolution quality checks on the host.
struct FrameQuality {
  float mean = 0, contrast = 0, sharpness = 0, movement = 255;
  bool decoded = false, poor = false, duplicate = false;
  uint8_t signature[32 * 24] = {};
};

inline FrameQuality measureFrame(const uint8_t *rgb, int width, int height,
                                 const FrameQuality *reference) {
  FrameQuality q;
  if (!rgb || width < 3 || height < 3) return q;
  auto gray = [&](int x, int y) -> int {
    const uint8_t *p = rgb + (y * width + x) * 3;
    return (77 * p[0] + 150 * p[1] + 29 * p[2]) >> 8;
  };
  double sum = 0, squared = 0, edges = 0;
  int dark = 0, white = 0, count = 0;
  // Sampling reduces work; all coordinates refer to the decoded thumbnail.
  for (int y = 1; y < height - 1; y += 2) {
    for (int x = 1; x < width - 1; x += 2) {
      int value = gray(x, y);
      sum += value; squared += value * value;
      dark += value < 8; white += value > 250; ++count;
      edges += abs(4 * value - gray(x-1,y) - gray(x+1,y) - gray(x,y-1) - gray(x,y+1));
    }
  }
  q.mean = sum / count;
  q.contrast = sqrt(fmax(0.0, squared / count - q.mean * q.mean));
  q.sharpness = edges / count;
  q.decoded = true;
  q.poor = dark > count * .98 || white > count * .995 || (q.contrast < 3 && q.sharpness < 1);
  float difference = 0;
  for (int y = 0; y < 24; ++y) for (int x = 0; x < 32; ++x) {
    int i = y * 32 + x;
    q.signature[i] = gray((2*x+1)*width/64, (2*y+1)*height/48);
    if (reference && reference->decoded)
      difference += fabs((float)q.signature[i] - reference->signature[i] - (q.mean - reference->mean));
  }
  if (reference && reference->decoded) {
    q.movement = difference / (32 * 24);
    // Sensor noise and tiny hand movements remain visible after downscaling.
    // Keep this tolerance low; the pipeline still forces periodic originals.
    q.duplicate = q.movement < 4 && fabs(q.mean - reference->mean) < 4 &&
                  q.sharpness <= reference->sharpness * 1.15 + .2;
    // Avoid harsh absolute blur thresholds on thin or faint text.
    if (q.movement < 8 && reference->sharpness > 4 && q.sharpness < reference->sharpness * .45)
      q.poor = true;
  }
  return q;
}
