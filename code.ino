/*
 * Patient Health Monitoring Dashboard — ESP32 Firmware
 * =====================================================
 * Sensor  : AD8232 (ECG) + MAX30102 (BPM & SpO2)
 * Output  : JSON ke Serial USB → dibaca Python backend
 * Format  : {"ecg":<float>,"bpm":<int>,"spo2":<int>}
 *
 * Catatan: Serial Plotter tidak bisa digunakan bersamaan
 * karena output sudah diformat JSON untuk Python.
 */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "MAX30105.h"
#include "heartRate.h"

// =====================
// PIN
// =====================
#define ECG_PIN 34

// =====================
// OLED
// =====================
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

// =====================
// MAX30102
// =====================
MAX30105 particleSensor;

const byte RATE_SIZE = 4;
byte rates[RATE_SIZE];
byte rateSpot = 0;
long lastBeat = 0;

float beatsPerMinute = 0;
int beatAvg = 0;
long irValue = 0;
long redValue = 0;

// SpO2
float spo2 = 0;
float lastValidSpO2 = 0;
float irDC = 0, redDC = 0;
float irAC = 0, redAC = 0;

// =====================
// ECG AD8232
// =====================
// Sampling 250 Hz → interval 4000 µs
const unsigned long ecgSampleInterval = 4000;
unsigned long lastECGSample = 0;

float ecgBaseline = 2048;
float ecgHP = 0;
float ecgFiltered = 0;

// Notch filter 50 Hz (fs = 250 Hz)
float b0 = 0.97948276, b1 = -0.60535364, b2 = 0.97948276;
float a1 = -0.60535364, a2 = 0.95896552;
float notchX1 = 0, notchX2 = 0;
float notchY1 = 0, notchY2 = 0;

// =====================
// TIMER OLED
// =====================
unsigned long lastOLEDUpdate = 0;
const unsigned long oledInterval = 500;   // Update OLED tiap 500 ms

// =====================
// TIMER JSON SERIAL
// =====================
// ECG dikirim tiap sample (250 Hz) → terlalu cepat jika digabung BPM/SpO2
// Solusi: ECG tetap 250 Hz, BPM & SpO2 ikut di setiap paket JSON
// Python mengambil nilai BPM/SpO2 dari paket terakhir yang valid.
bool newECGReady = false;
float pendingECG = 0;

// =====================
// SETUP
// =====================
void setup() {
  Serial.begin(115200);

  pinMode(ECG_PIN, INPUT);
  analogReadResolution(12);
  analogSetPinAttenuation(ECG_PIN, ADC_11db);

  Wire.begin(21, 22);

  // OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    while (1);   // OLED tidak ditemukan, berhenti
  }

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("Initializing...");
  display.display();

  // MAX30102
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    display.clearDisplay();
    display.setCursor(0, 0);
    display.println("MAX30102 ERROR");
    display.setCursor(0, 16);
    display.println("Check wiring");
    display.display();
    while (1);
  }

  particleSensor.setup();
  particleSensor.setPulseAmplitudeRed(0x0A);
  particleSensor.setPulseAmplitudeGreen(0);

  for (byte i = 0; i < RATE_SIZE; i++) rates[i] = 0;

  delay(1000);
  ecgBaseline = analogRead(ECG_PIN);

  display.clearDisplay();
  display.display();
}

// =====================
// LOOP
// =====================
void loop() {
  readECG();       // Cek sample ECG (timer-based, 250 Hz)
  readMAX30102();  // Baca BPM + SpO2 dari MAX30102

  // Kirim JSON ke Serial tiap ada sample ECG baru
  if (newECGReady) {
    newECGReady = false;
    sendJSON();
  }

  // Update OLED lebih lambat (tiap 500 ms)
  if (millis() - lastOLEDUpdate >= oledInterval) {
    lastOLEDUpdate = millis();
    updateOLED();
  }
}

// =====================
// BACA ECG
// =====================
void readECG() {
  unsigned long now = micros();
  if (now - lastECGSample < ecgSampleInterval) return;
  lastECGSample = now;

  float rawECG = analogRead(ECG_PIN);

  // High-pass filter (hapus DC baseline)
  ecgBaseline = 0.999f * ecgBaseline + 0.001f * rawECG;
  ecgHP = rawECG - ecgBaseline;

  // Notch filter 50 Hz
  ecgFiltered = b0 * ecgHP
              + b1 * notchX1
              + b2 * notchX2
              - a1 * notchY1
              - a2 * notchY2;

  notchX2 = notchX1; notchX1 = ecgHP;
  notchY2 = notchY1; notchY1 = ecgFiltered;

  pendingECG   = ecgFiltered;
  newECGReady  = true;
}

// =====================
// BACA MAX30102
// =====================
void readMAX30102() {
  irValue  = particleSensor.getIR();
  redValue = particleSensor.getRed();

  // BPM (metode deteksi beat dari library)
  if (checkForBeat(irValue)) {
    long delta = millis() - lastBeat;
    lastBeat = millis();

    beatsPerMinute = 60.0f / (delta / 1000.0f);

    if (beatsPerMinute > 20 && beatsPerMinute < 255) {
      rates[rateSpot++] = (byte)beatsPerMinute;
      rateSpot %= RATE_SIZE;

      beatAvg = 0;
      for (byte x = 0; x < RATE_SIZE; x++) beatAvg += rates[x];
      beatAvg /= RATE_SIZE;
    }
  }

  // SpO2 estimasi sederhana (R-ratio)
  if (irValue > 50000 && redValue > 10000) {
    irDC  = 0.95f * irDC  + 0.05f * irValue;
    redDC = 0.95f * redDC + 0.05f * redValue;

    irAC  = abs(irValue  - irDC);
    redAC = abs(redValue - redDC);

    if (irDC > 0 && redDC > 0 && irAC > 0 && redAC > 0) {
      float ratio = (redAC / redDC) / (irAC / irDC);
      spo2 = 110.0f - 25.0f * ratio;
      spo2 = constrain(spo2, 70.0f, 100.0f);
      lastValidSpO2 = spo2;
    }
  } else {
    lastValidSpO2 = 0;
  }
}

// =====================
// KIRIM JSON KE SERIAL
// =====================
void sendJSON() {
  // BPM: 0 jika jari tidak terdeteksi
  int bpmOut  = (irValue > 50000 && beatAvg > 0) ? beatAvg : 0;
  // SpO2: 0 jika jari tidak terdeteksi
  int spo2Out = (lastValidSpO2 > 0) ? (int)lastValidSpO2 : 0;

  // Format JSON satu baris — Python membaca per-baris
  Serial.print("{\"ecg\":");
  Serial.print(pendingECG, 2);    // 2 desimal cukup
  Serial.print(",\"bpm\":");
  Serial.print(bpmOut);
  Serial.print(",\"spo2\":");
  Serial.print(spo2Out);
  Serial.println("}");
}

// =====================
// UPDATE OLED
// =====================
void updateOLED() {
  display.clearDisplay();

  // Garis pembagi horizontal
  display.drawLine(0, 32, 127, 32, SSD1306_WHITE);

  // ── Bagian atas: BPM ──
  display.setTextSize(1);
  display.setCursor(4, 2);
  display.print("BPM");

  display.setTextSize(2);
  if (irValue < 50000 || beatAvg == 0) {
    display.setCursor(48, 9);
    display.print("--");
  } else {
    display.setCursor(beatAvg < 100 ? 58 : 50, 9);
    display.print(beatAvg);
  }

  // ── Bagian bawah: SpO2 ──
  display.setTextSize(1);
  display.setCursor(4, 36);
  display.print("SpO2");

  display.setTextSize(2);
  if (irValue < 50000 || lastValidSpO2 <= 0) {
    display.setCursor(48, 43);
    display.print("--%");
  } else {
    int s = (int)lastValidSpO2;
    display.setCursor(s >= 100 ? 42 : 50, 43);
    display.print(s);
    display.print("%");
  }

  display.display();
}
