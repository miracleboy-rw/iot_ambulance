/*
 * ============================================
 *  IoT Health Monitoring System
 *  ESP32 + MAX30102 + AD8232 + OLED 0.96"
 * ============================================
 *  Sensor:
 *    - MAX30102 : BPM & SpO2 (I2C: SDA=21, SCL=22)
 *    - AD8232   : EKG/ECG (Analog pin 35)
 *    - OLED     : 128x64, alamat 0x3C (I2C)
 *  Koneksi:
 *    - WiFi → HTTP POST ke Flask server
 * ============================================
 */

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "MAX30105.h"
#include "heartRate.h"
#include "spo2_algorithm.h"

// ==================== KONFIGURASI ====================

// WiFi
const char* ssid     = "SSID_ANDA";
const char* password = "PASSWORD_ANDA";

// Flask Server - ganti IP sesuai komputer Anda
const char* serverURL = "http://192.168.1.100:5000/api/data";

// Pin AD8232 (EKG)
#define ECG_PIN 35

// OLED
#define SCREEN_WIDTH  128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
#define OLED_ADDRESS  0x3C

// ==================== OBJEK ====================

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
MAX30105 particleSensor;

// ==================== VARIABEL BPM ====================

const byte RATE_SIZE = 4;
byte rates[RATE_SIZE];
byte rateSpot = 0;
long lastBeat = 0;
float beatsPerMinute = 0;
int beatAvg = 0;

// ==================== VARIABEL SpO2 ====================

uint32_t irBuffer[100];
uint32_t redBuffer[100];
int32_t spo2Value;
int8_t  validSPO2;
int32_t heartRateValue;
int8_t  validHeartRate;

// ==================== VARIABEL EKG ====================

int ecgValue = 0;

// ==================== VARIABEL GRAFIK OLED ====================

#define GRAPH_WIDTH 128
int ecgGraph[GRAPH_WIDTH];
int graphIndex = 0;

// ==================== TIMING ====================

unsigned long lastSendTime = 0;
const unsigned long sendInterval = 1000;  // kirim data tiap 1 detik

unsigned long lastOLEDTime = 0;
const unsigned long oledInterval = 100;   // update OLED tiap 100ms

// ==================== SETUP ====================

void setup() {
  Serial.begin(115200);
  Serial.println("IoT Health Monitor - Starting...");

  // Inisialisasi I2C
  Wire.begin(21, 22);

  // Inisialisasi OLED
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS)) {
    Serial.println("OLED gagal!");
    while (1);
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(10, 20);
  display.println("Initializing...");
  display.display();

  // Inisialisasi MAX30102
  if (!particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("MAX30102 tidak ditemukan!");
    display.clearDisplay();
    display.setCursor(0, 20);
    display.println("MAX30102 ERROR!");
    display.display();
    while (1);
  }

  // Konfigurasi MAX30102
  particleSensor.setup();
  particleSensor.setPulseAmplitudeRed(0x0A);   // LED merah rendah untuk indikator
  particleSensor.setPulseAmplitudeGreen(0);     // LED hijau mati
  particleSensor.enableDIETEMPRDY();

  // Inisialisasi pin EKG
  analogSetAttenuation(ADC_11db);
  pinMode(ECG_PIN, INPUT);

  // Inisialisasi array grafik EKG
  for (int i = 0; i < GRAPH_WIDTH; i++) {
    ecgGraph[i] = 0;
  }

  // Koneksi WiFi
  connectWiFi();

  // Tampilan awal OLED
  display.clearDisplay();
  display.setCursor(20, 10);
  display.println("System Ready!");
  display.setCursor(15, 30);
  display.println("Health Monitor");
  display.display();
  delay(1500);
}

// ==================== LOOP ====================

void loop() {
  // Baca sensor MAX30102 (BPM)
  readMAX30102();

  // Baca sensor AD8232 (EKG)
  ecgValue = analogRead(ECG_PIN);

  // Simpan data EKG untuk grafik OLED
  ecgGraph[graphIndex] = map(ecgValue, 0, 4095, 0, 30);
  graphIndex = (graphIndex + 1) % GRAPH_WIDTH;

  // Update OLED
  unsigned long currentTime = millis();
  if (currentTime - lastOLEDTime >= oledInterval) {
    lastOLEDTime = currentTime;
    updateOLED();
  }

  // Kirim data ke Flask server
  if (currentTime - lastSendTime >= sendInterval) {
    lastSendTime = currentTime;
    sendDataToServer();
  }
}

// ==================== FUNGSI BACA MAX30102 ====================

void readMAX30102() {
  long irValue = particleSensor.getIR();

  if (checkForBeat(irValue) == true) {
    long delta = millis() - lastBeat;
    lastBeat = millis();

    beatsPerMinute = 60 / (delta / 1000.0);

    if (beatsPerMinute < 255 && beatsPerMinute > 20) {
      rates[rateSpot++] = (byte)beatsPerMinute;
      rateSpot %= RATE_SIZE;

      beatAvg = 0;
      for (byte x = 0; x < RATE_SIZE; x++) {
        beatAvg += rates[x];
      }
      beatAvg /= RATE_SIZE;
    }
  }

  // Baca SpO2 secara periodik
  static unsigned long lastSpO2Time = 0;
  if (millis() - lastSpO2Time >= 5000) {
    lastSpO2Time = millis();
    readSpO2();
  }

  // Jika tidak ada jari terdeteksi
  if (irValue < 50000) {
    beatsPerMinute = 0;
    beatAvg = 0;
    spo2Value = 0;
  }
}

// ==================== FUNGSI BACA SpO2 ====================

void readSpO2() {
  // Ambil 100 sampel
  for (byte i = 0; i < 100; i++) {
    while (particleSensor.available() == false)
      particleSensor.check();

    redBuffer[i] = particleSensor.getRed();
    irBuffer[i]  = particleSensor.getIR();
    particleSensor.nextSample();
  }

  // Hitung SpO2
  maxim_heart_rate_and_oxygen_saturation(
    irBuffer, 100, redBuffer,
    &spo2Value, &validSPO2,
    &heartRateValue, &validHeartRate
  );

  // Validasi
  if (validSPO2 == 0 || spo2Value < 0 || spo2Value > 100) {
    spo2Value = 0;
  }
}

// ==================== FUNGSI UPDATE OLED ====================

void updateOLED() {
  display.clearDisplay();

  // Header
  display.setTextSize(1);
  display.setCursor(5, 0);
  display.print("Health Monitor");

  // Garis pemisah
  display.drawLine(0, 10, 128, 10, SSD1306_WHITE);

  // BPM
  display.setCursor(0, 14);
  display.print("BPM: ");
  if (beatAvg > 0) {
    display.print(beatAvg);
  } else {
    display.print("--");
  }

  // SpO2
  display.setCursor(70, 14);
  display.print("SpO2:");
  if (spo2Value > 0) {
    display.print(spo2Value);
    display.print("%");
  } else {
    display.print("--");
  }

  // Garis pemisah
  display.drawLine(0, 25, 128, 25, SSD1306_WHITE);

  // Label EKG
  display.setCursor(0, 28);
  display.print("EKG:");

  // Grafik EKG mini di OLED
  int graphY = 33;
  int graphH = 30;
  for (int i = 0; i < GRAPH_WIDTH - 1; i++) {
    int idx1 = (graphIndex + i) % GRAPH_WIDTH;
    int idx2 = (graphIndex + i + 1) % GRAPH_WIDTH;
    int y1 = graphY + graphH - ecgGraph[idx1];
    int y2 = graphY + graphH - ecgGraph[idx2];
    display.drawLine(i, y1, i + 1, y2, SSD1306_WHITE);
  }

  display.display();
}

// ==================== FUNGSI KONEKSI WIFI ====================

void connectWiFi() {
  Serial.print("Menghubungkan ke WiFi");
  display.clearDisplay();
  display.setCursor(5, 10);
  display.println("Connecting WiFi...");
  display.setCursor(5, 30);
  display.println(ssid);
  display.display();

  WiFi.begin(ssid, password);

  int timeout = 0;
  while (WiFi.status() != WL_CONNECTED && timeout < 30) {
    delay(500);
    Serial.print(".");
    timeout++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi terhubung!");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());

    display.clearDisplay();
    display.setCursor(10, 10);
    display.println("WiFi Connected!");
    display.setCursor(10, 30);
    display.print("IP: ");
    display.println(WiFi.localIP());
    display.display();
    delay(1500);
  } else {
    Serial.println("\nWiFi gagal! Lanjut tanpa koneksi.");
    display.clearDisplay();
    display.setCursor(10, 20);
    display.println("WiFi Failed!");
    display.setCursor(10, 35);
    display.println("Running offline");
    display.display();
    delay(1500);
  }
}

// ==================== FUNGSI KIRIM DATA KE SERVER ====================

void sendDataToServer() {
  if (WiFi.status() != WL_CONNECTED) {
    // Coba reconnect
    Serial.println("WiFi terputus, reconnecting...");
    connectWiFi();
    return;
  }

  HTTPClient http;
  http.begin(serverURL);
  http.addHeader("Content-Type", "application/json");

  // Buat JSON payload
  String jsonPayload = "{";
  jsonPayload += "\"bpm\":" + String(beatAvg) + ",";
  jsonPayload += "\"spo2\":" + String(spo2Value) + ",";
  jsonPayload += "\"ecg\":" + String(ecgValue);
  jsonPayload += "}";

  int httpCode = http.POST(jsonPayload);

  if (httpCode > 0) {
    Serial.print("Data terkirim! HTTP: ");
    Serial.println(httpCode);
  } else {
    Serial.print("Gagal kirim data. Error: ");
    Serial.println(http.errorToString(httpCode));
  }

  http.end();
}
